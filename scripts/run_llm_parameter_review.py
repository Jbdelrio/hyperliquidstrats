"""
run_llm_parameter_review.py — Rule-based "LLM" parameter review agent (v1).

Reads the current logs / runtime state and produces:
  - reports/llm_parameter_review.md       (markdown rationale, per-strategy)
  - runtime/proposed_config_patch.json    (machine-readable patch proposals)

Each patch follows the schema requested in the spec:
  {
    "strategy": "...",
    "parameter": "...",
    "current_value": <num>,
    "proposed_value": <num>,
    "reason": "...",
    "expected_effect": {"trade_frequency": "...", "risk": "...", "net_pnl": "..."},
    "confidence": 0.0 .. 1.0,
    "apply_automatically": false
  }

The agent **never** writes to the live config — only to the proposed_patch
file. Apply manually after review (`scripts/apply_config_patch.py` could be a
future companion).

v1 is rule-based and deterministic: it ranks each strategy's top rejection
reason, maps it to a bounded parameter adjustment, and computes a confidence
from the fraction of rejections explained by that reason. The output format
is stable, so a real LLM call can replace `_propose_patches()` later without
touching the rest of the pipeline.

Usage from the repo root:
    python scripts/run_llm_parameter_review.py
    python scripts/run_llm_parameter_review.py --since 1779390020
    python scripts/run_llm_parameter_review.py --logs logs --runtime runtime \\
                                                --out reports/llm_review.md
"""
from __future__ import annotations

import argparse
import collections
import csv
import datetime as _dt
import json
import os
import sys
import time
from pathlib import Path


# ---------------------------------------------------------------------------
# Heuristic catalogue — reason head → (param, multiplier, risk, frequency_eff)
# ---------------------------------------------------------------------------
# Multiplier: factor applied to current value. <1.0 reduces (e.g. cooldown
# from 300 → 225). >1.0 raises (e.g. spread cap from 1.5 → 1.875).
# Risk: "low" / "moderate" / "high" — surfaced in the report.
# Frequency effect: how the change is expected to affect trade frequency.
_HEURISTICS = {
    "warmup":                  None,           # no patch, just info
    "spread_too_wide":         {"global": ("max_spread_bps_by_symbol", 1.25, "low",  "increase")},
    "low_volume":              {"global": ("min_volume_30s_usd_by_symbol", 0.75, "moderate", "increase")},
    "latency_p95":             None,           # feed quality, no param fix
    "rv_too_high":             {"global": ("max_realized_vol_60s_bps", 1.25, "moderate", "increase")},
    "toxicity_high":           {"global": ("max_toxicity_score", 1.05, "moderate", "increase")},
    "liquidity_low":           {"global": ("min_liquidity_score", 0.85, "moderate", "increase")},
    "ofi_against_long":        {"global": ("ofi_block_threshold", 1.15, "low",  "increase")},
    "ofi_against_short":       {"global": ("ofi_block_threshold", 1.15, "low",  "increase")},
    "depth_against_long":      {"global": ("depth_block_threshold", 1.15, "low",  "increase")},
    "depth_against_short":     {"global": ("depth_block_threshold", 1.15, "low",  "increase")},
    "cooldown":                {"exec":   ("cooldown_win_s",  0.75, "low",  "increase"),
                                "exec2":  ("cooldown_loss_s", 0.75, "moderate", "increase")},
    "throttle":                {"throttle": ("min_seconds_between_entries_per_strategy", 0.66, "low", "increase")},
    "net_too_low":             {"exec":   ("min_expected_net_profit_usd", 0.66, "moderate", "increase")},
    "rr_too_low":              {"exec":   ("min_reward_risk_ratio", 0.90, "moderate", "increase")},
    "max_positions_reached":   {"strategy": ("max_positions", 2.0, "low", "increase")},
    "edge_below_costs":        {"strategy_params": ("take_profit_bps", 1.20, "moderate", "increase")},
    "in_position":             None,
    "pending":                 None,
    "data_stale":              None,
    "consecutive_loss_limit":  None,
    "daily_loss_limit":        None,
    "NO_TRADE_WARMUP":         None,
    "NO_TRADE_SPREAD_TOO_WIDE":     {"strategy_params": ("max_spread_bps", 1.25, "low", "increase")},
    "NO_TRADE_NO_VOL":              {"strategy_params": ("min_rv_300s_bps", 0.75, "moderate", "increase")},
    "NO_TRADE_SIGNAL_TOO_WEAK":     {"strategy_params": ("long_threshold", 0.97, "moderate", "increase")},
    "NO_TRADE_OBI_NOT_ALIGNED":     {"strategy_params": ("min_obi_long", 0.75, "low", "increase")},
    "NO_TRADE_FLOW_NOT_ALIGNED":    {"strategy_params": ("min_flow_long", 0.75, "low", "increase")},
}


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def _parse_ts(raw) -> float:
    if raw in (None, "", "nan"):
        return 0.0
    s = str(raw)
    try:
        return float(s)
    except ValueError:
        pass
    try:
        return _dt.datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    try:
        with open(path, encoding="utf-8", errors="replace", newline="") as fh:
            return list(csv.DictReader(fh))
    except Exception:
        return []


def _read_json(path: Path):
    if not path.exists():
        return None
    try:
        return json.load(open(path, encoding="utf-8"))
    except Exception:
        return None


def _resolve_since(args_since, runtime: Path) -> float:
    if args_since:
        return _parse_ts(args_since)
    ecfg = _read_json(runtime / "engine_config.json") or {}
    ts = float(ecfg.get("started_at") or 0)
    return ts if ts > 0 else (time.time() - 3600.0)


# ---------------------------------------------------------------------------
# Per-strategy rejection / performance aggregation
# ---------------------------------------------------------------------------

def _aggregate(dec_rows, fills_rows, since: float):
    strat_dec = collections.defaultdict(
        lambda: {"raw": 0, "placed": 0, "rejected": 0,
                 "reasons": collections.Counter()})
    for r in dec_rows:
        ts = _parse_ts(r.get("timestamp"))
        if ts < since - 5.0:
            continue
        s = (r.get("strategy") or "").strip() or "(unattributed)"
        d = (r.get("decision") or "").strip()
        st = strat_dec[s]
        st["raw"] += 1
        if d == "PLACE":
            st["placed"] += 1
        elif d in ("SKIP", "FILTER_SKIP"):
            st["rejected"] += 1
            reason = (r.get("reason") or r.get("blocked_reason") or "").strip()
            if reason:
                # bucket head before any ":<detail>"
                st["reasons"][reason.split(":", 1)[0]] += 1

    strat_perf = collections.defaultdict(
        lambda: {"trades": 0, "net": 0.0, "wins": 0, "losses": 0})
    for r in fills_rows:
        ts = _parse_ts(r.get("ts"))
        if ts < since - 5.0:
            continue
        s = (r.get("strategy") or "").strip() or "(unattributed)"
        try:
            net = float(r.get("net") or 0.0)
        except ValueError:
            net = 0.0
        sp = strat_perf[s]
        sp["trades"] += 1
        sp["net"] += net
        sp["wins"] += int(net > 0)
        sp["losses"] += int(net < 0)
    return strat_dec, strat_perf


# ---------------------------------------------------------------------------
# Patch proposal — the swappable rule-based "LLM"
# ---------------------------------------------------------------------------

def _propose_patches(strat_dec: dict, strat_perf: dict, preset: dict,
                     min_rejects: int = 30) -> list[dict]:
    """Return a list of patches conforming to the schema.

    Replace this function with a call to a real LLM later — the input
    (`strat_dec`, `strat_perf`, `preset`) and the output schema are stable."""
    patches: list[dict] = []
    preset_strats = {s["name"]: s for s in preset.get("strategies", [])}

    # 1. Per-strategy rejection-driven patches.
    for name, dec in strat_dec.items():
        if name == "(unattributed)" or dec["rejected"] < min_rejects:
            continue
        top = dec["reasons"].most_common(1)
        if not top:
            continue
        reason_head, n = top[0]
        share = n / max(dec["rejected"], 1)
        rule = _HEURISTICS.get(reason_head)
        if rule is None:
            continue
        cfg_entry = preset_strats.get(name) or {}
        for scope, (param, mult, risk, freq) in rule.items():
            p = _build_patch(name, scope, param, mult, risk, freq,
                              reason_head, n, share, preset, cfg_entry)
            if p is not None:
                patches.append(p)

    # 2. Global preset-level patches when MULTIPLE strategies share a head.
    head_counts: collections.Counter = collections.Counter()
    for dec in strat_dec.values():
        for k, v in dec["reasons"].items():
            head_counts[k] += v
    for head, total in head_counts.most_common(5):
        rule = _HEURISTICS.get(head)
        if rule is None:
            continue
        for scope, (param, mult, risk, freq) in rule.items():
            if scope in ("global", "exec", "exec2", "throttle"):
                p = _build_global_patch(scope, param, mult, risk, freq,
                                        head, total, preset)
                if p is not None and not _patch_already_in(patches, p):
                    patches.append(p)
    return patches


def _build_patch(strat: str, scope: str, param: str, mult: float,
                 risk: str, freq: str, reason: str, n: int, share: float,
                 preset: dict, cfg_entry: dict):
    if scope == "strategy":
        cur = cfg_entry.get(param)
        if cur is None:
            return None
        new = int(round(cur * mult)) if isinstance(cur, int) else cur * mult
    elif scope == "strategy_params":
        cur = (cfg_entry.get("params") or {}).get(param)
        if cur is None:
            return None
        new = cur * mult
    else:
        return None     # global handled separately
    confidence = round(min(0.85, share * 0.7 + min(1.0, n / 200.0) * 0.15), 3)
    return {
        "strategy": strat,
        "parameter": f"strategies[{strat}].{'params.' if scope == 'strategy_params' else ''}{param}",
        "current_value": cur,
        "proposed_value": round(new, 6) if isinstance(new, float) else new,
        "reason": (f"{reason} accounts for {share:.0%} of rejections ({n}) on "
                   f"{strat}; bounded {('+' if mult > 1 else '')}"
                   f"{(mult - 1) * 100:.0f}% adjustment of {param}."),
        "expected_effect": {
            "trade_frequency": freq,
            "risk": risk,
            "net_pnl": "unknown_requires_ab_test",
        },
        "confidence": confidence,
        "apply_automatically": False,
    }


def _build_global_patch(scope: str, param: str, mult: float, risk: str,
                        freq: str, reason: str, n: int, preset: dict):
    if scope == "global":
        mqg = preset.get("market_quality_gate") or {}
        cur = mqg.get(param)
        if cur is None:
            return None
        path = f"market_quality_gate.{param}"
    elif scope == "exec":
        ef = preset.get("execution_filters") or {}
        cur = ef.get(param)
        if cur is None:
            return None
        path = f"execution_filters.{param}"
    elif scope == "exec2":
        ef = preset.get("execution_filters") or {}
        cur = ef.get(param)
        if cur is None:
            return None
        path = f"execution_filters.{param}"
    elif scope == "throttle":
        th = preset.get("decision_throttle") or {}
        cur = th.get(param)
        if cur is None:
            return None
        path = f"decision_throttle.{param}"
    else:
        return None
    if isinstance(cur, dict):
        return None         # skip per-symbol nested dicts in v1
    new = cur * mult
    confidence = round(min(0.80, min(1.0, n / 1000.0) * 0.6 + 0.20), 3)
    return {
        "strategy": "(global)",
        "parameter": path,
        "current_value": cur,
        "proposed_value": round(new, 6) if isinstance(new, float) else new,
        "reason": (f"{reason} occurred {n} times across strategies; bounded "
                   f"{('+' if mult > 1 else '')}{(mult - 1) * 100:.0f}% "
                   f"adjustment of {param}."),
        "expected_effect": {
            "trade_frequency": freq,
            "risk": risk,
            "net_pnl": "unknown_requires_ab_test",
        },
        "confidence": confidence,
        "apply_automatically": False,
    }


def _patch_already_in(patches: list[dict], p: dict) -> bool:
    for q in patches:
        if q.get("parameter") == p["parameter"]:
            return True
    return False


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _render_markdown(strat_dec, strat_perf, patches, preset_name, since) -> str:
    out = ["# LLM Parameter Review (rule-based v1)\n"]
    out.append(f"*Generated {_dt.datetime.now().isoformat(timespec='seconds')}*\n")
    out.append(f"Preset analysed : `{preset_name}`")
    out.append(f"Window since    : "
               f"{_dt.datetime.fromtimestamp(since).isoformat(timespec='seconds')}\n")

    out.append("## 1. Per-strategy performance + rejection summary\n")
    out.append("| Strategy | Raw | Placed | Rejected | Main rejection | Trades | Net PnL | WR |")
    out.append("|---|---|---|---|---|---|---|---|")
    names = sorted(set(strat_dec.keys()) | set(strat_perf.keys()))
    for name in names:
        d = strat_dec.get(name, {"raw": 0, "placed": 0, "rejected": 0,
                                  "reasons": collections.Counter()})
        p = strat_perf.get(name, {"trades": 0, "net": 0.0, "wins": 0, "losses": 0})
        top = d["reasons"].most_common(1)
        top_s = (f"`{top[0][0]}` ({top[0][1]})") if top else "—"
        wr = (100.0 * p["wins"] / max(p["trades"], 1)) if p["trades"] else 0.0
        out.append(f"| {name} | {d['raw']} | {d['placed']} | {d['rejected']} | "
                   f"{top_s} | {p['trades']} | ${p['net']:+.4f} | {wr:.0f}% |")

    out.append("\n## 2. Proposed parameter patches\n")
    if not patches:
        out.append("*No actionable patches — either samples are too small or "
                   "all rejections fall under reasons without a rule "
                   "(warmup, data_stale, etc.).*")
    else:
        out.append(f"{len(patches)} proposal(s). **Nothing is applied "
                   "automatically.** Review each, then apply manually.\n")
        for i, p in enumerate(patches, 1):
            out.append(f"### {i}. `{p['parameter']}` "
                       f"(strategy: {p['strategy']})")
            out.append(f"- current : `{p['current_value']}`")
            out.append(f"- proposed: `{p['proposed_value']}`")
            out.append(f"- reason  : {p['reason']}")
            ee = p["expected_effect"]
            out.append(f"- expected: frequency **{ee['trade_frequency']}**, "
                       f"risk **{ee['risk']}**, PnL **{ee['net_pnl']}**")
            out.append(f"- confidence: **{p['confidence']:.2f}**")
            out.append(f"- apply_automatically: **{p['apply_automatically']}**\n")

    out.append("\n## 3. Notes & caveats\n")
    out.append("- This is **risk-management tuning**, not alpha discovery. Lower"
               " thresholds let more trades fire; they do not create edge.\n"
               "- Multiplications are bounded (`±15–35 %`). No floor is taken"
               " below a safe minimum here, but you should still validate"
               " each patch in A/B against the baseline.\n"
               "- A patch with `confidence < 0.4` is a hint, not an "
               "instruction — needs more data.\n"
               "- If `latency_p95` dominates rejections, the fix is in"
               " `data/orderbook_manager.py` (feed health), not in params.")

    out.append("\n---\n*Rule-based v1 — swap `_propose_patches()` with an LLM"
                " call to upgrade.*")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--logs",    default="logs")
    ap.add_argument("--runtime", default="runtime")
    ap.add_argument("--out",     default="reports/llm_parameter_review.md")
    ap.add_argument("--patch",   default="runtime/proposed_config_patch.json")
    ap.add_argument("--since",   default=None)
    ap.add_argument("--min-rejects", type=int, default=20,
                    help="Skip strategies with fewer than N rejections.")
    args = ap.parse_args()

    logs = Path(args.logs)
    runtime = Path(args.runtime)
    since = _resolve_since(args.since, runtime)

    ecfg = _read_json(runtime / "engine_config.json") or {}
    preset_path = ecfg.get("config_path")
    repo = Path(__file__).resolve().parents[1]
    preset = {}
    if preset_path:
        p = Path(preset_path)
        if not p.is_absolute():
            p = repo / preset_path
        preset = _read_json(p) or {}
    # Fallback: scan known presets if engine_config is gone (--fresh / engine down).
    if not preset:
        for candidate in ("config/presets/paper_500_all_active.json",
                          "config/presets/paper_500_clean.json"):
            preset = _read_json(repo / candidate) or {}
            if preset:
                preset_path = candidate
                break

    dec = _read_csv(logs / "decisions_v9.csv")
    fills = _read_csv(logs / "fills_v9.csv")
    strat_dec, strat_perf = _aggregate(dec, fills, since)
    patches = _propose_patches(strat_dec, strat_perf, preset,
                                min_rejects=args.min_rejects)

    # Write the patch JSON.
    patch_path = Path(args.patch)
    patch_path.parent.mkdir(parents=True, exist_ok=True)
    with open(patch_path, "w", encoding="utf-8") as fh:
        json.dump({"generated_at": time.time(), "patches": patches,
                   "source_preset": preset_path}, fh, indent=2)

    # Write the markdown report.
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_render_markdown(strat_dec, strat_perf, patches,
                                     preset_path or "(unknown)", since),
                   encoding="utf-8")
    print(f"[llm_review] {len(patches)} patch(es) proposed")
    print(f"[llm_review] report  -> {out}")
    print(f"[llm_review] patches -> {patch_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
