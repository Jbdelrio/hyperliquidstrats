"""
diagnose_trading_frequency.py — One-stop diagnostic for "why doesn't the bot
trade enough".

Aggregates the current state of:
  - logs/decisions_v9.csv  (raw signals, skips, filter blocks)
  - logs/fills_v9.csv      (executed trades)
  - logs/risk_events.csv   (ledger / portfolio block reasons)
  - logs/engine_v9.log     (recent errors / gate blocks)
  - runtime/strategy_status.json   (per-strategy state, capital, positions)
  - runtime/data_feed_status.json  (feed health, MQG / DecisionThrottle stats)
  - runtime/engine_config.json     (session start, loaded preset)
  - runtime/regime_status.json     (market regime + active adjustments)

into a single Markdown report. Read-only. Safe to run while the engine is up.

Usage from the repo root:
    python scripts/diagnose_trading_frequency.py
    python scripts/diagnose_trading_frequency.py --minutes 10
    python scripts/diagnose_trading_frequency.py --since 1779390020
    python scripts/diagnose_trading_frequency.py --out reports/diagnostic.md

When `--minutes` is given, the script first sleeps that long, then snapshots.
Use it to capture a fresh window of live behaviour. Default = 0 (snapshot now).
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


def _parse_ts(raw) -> float:
    """Accept epoch float, epoch int, or ISO 8601 (local-time naive)."""
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


def _resolve_since(args_since, runtime_dir: Path) -> float:
    """Determine the cutoff epoch for current-session rows."""
    if args_since:
        return _parse_ts(args_since)
    ecfg = runtime_dir / "engine_config.json"
    if ecfg.exists():
        try:
            d = json.load(open(ecfg, encoding="utf-8"))
            ts = float(d.get("started_at") or d.get("ts") or 0)
            if ts > 0:
                return ts
        except Exception:
            pass
    # Fallback: pid file mtime (engine writes it at startup).
    pid = runtime_dir / "engine.pid"
    if pid.exists():
        return pid.stat().st_mtime
    # Last resort: 1 h ago.
    return time.time() - 3600.0


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


def _filter_after(rows: list[dict], ts_keys: list[str], cutoff: float) -> list[dict]:
    out = []
    for r in rows:
        ts_raw = ""
        for k in ts_keys:
            if k in r and r[k]:
                ts_raw = r[k]
                break
        ts = _parse_ts(ts_raw)
        if ts >= cutoff - 5.0:
            out.append(r)
    return out


# ── Per-strategy aggregation ────────────────────────────────────────────────

def _aggregate_decisions(rows: list[dict]):
    """Return (per_strategy_counts, top_reasons_global)."""
    per_strat = collections.defaultdict(
        lambda: {"PLACE": 0, "SKIP": 0, "FILTER_SKIP": 0, "reasons": collections.Counter()})
    global_reasons = collections.Counter()
    for r in rows:
        d = (r.get("decision") or "").strip()
        s = (r.get("strategy") or "(unattributed)").strip() or "(unattributed)"
        reason = (r.get("reason") or r.get("blocked_reason") or "").strip()
        if d in per_strat[s]:
            per_strat[s][d] += 1
        if d in ("SKIP", "FILTER_SKIP") and reason:
            # Bucket head before any ":<detail>"
            head = reason.split(":", 1)[0]
            per_strat[s]["reasons"][head] += 1
            global_reasons[head] += 1
    return per_strat, global_reasons


def _aggregate_fills(rows: list[dict], since: float):
    """Return (per_strategy_stats, global_stats)."""
    per_strat = collections.defaultdict(lambda: {
        "n": 0, "net": 0.0, "wins": 0, "losses": 0, "first_ts": None, "last_ts": None,
    })
    for r in rows:
        s = (r.get("strategy") or "").strip() or "(unattributed)"
        try:
            net = float(r.get("net") or 0.0)
        except ValueError:
            net = 0.0
        ts = _parse_ts(r.get("ts"))
        st = per_strat[s]
        st["n"] += 1
        st["net"] += net
        st["wins"] += int(net > 0)
        st["losses"] += int(net < 0)
        if st["first_ts"] is None or ts < st["first_ts"]:
            st["first_ts"] = ts
        if st["last_ts"] is None or ts > st["last_ts"]:
            st["last_ts"] = ts
    now = time.time()
    for s, st in per_strat.items():
        span_h = max((now - max(since, st["first_ts"] or now)) / 3600.0, 1e-6)
        st["per_hour"] = st["n"] / span_h
    return per_strat


def _aggregate_risk_events(rows: list[dict]):
    """Return Counter of block_reason for rejected events."""
    c = collections.Counter()
    for r in rows:
        allowed = (r.get("allowed") or "").strip().lower()
        if allowed in ("0", "false"):
            br = (r.get("block_reason") or "").strip() or "unknown"
            c[br.split(":", 1)[0]] += 1
    return c


def _scan_log_blocks(log_path: Path, cutoff: float) -> dict:
    """Count [MQG] BLOCK lines per (strategy, reason)."""
    out = collections.Counter()
    if not log_path.exists():
        return out
    try:
        with open(log_path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "[MQG] BLOCK" not in line:
                    continue
                # Format: "... [MQG] BLOCK <Strategy> <SYM> <side> → <reason>"
                try:
                    after = line.split("[MQG] BLOCK", 1)[1].strip()
                    parts = after.split("→", 1) if "→" in after else after.split("->", 1)
                    head = parts[0].strip()
                    reason = (parts[1].strip() if len(parts) > 1 else "")
                    strat = head.split()[0] if head else "?"
                    out[(strat, reason.split(":", 1)[0])] += 1
                except Exception:
                    continue
    except Exception:
        pass
    return out


# ── Heuristic suggestion ────────────────────────────────────────────────────

_REASON_ACTION = {
    "warmup":            "Patience — warmup not reached yet (or reduce min_warmup_seconds).",
    "market_quality":    "Loosen MarketQualityGate thresholds (low_volume / latency_p95 first).",
    "spread_too_wide":   "Raise max_spread_bps for this strategy / symbol.",
    "low_volume":        "Lower min_volume_30s_usd_by_symbol; volume is naturally thin here.",
    "rv_too_high":       "Vol is too high right now — wait, or raise max_rv_30s.",
    "rv_too_low":        "Vol is too low — drop min_rv_300s_bps or wait.",
    "no_vol":            "Drop the rv floor; market is too quiet for this signal.",
    "edge_below_costs":  "Lower take_profit or accept tighter notional — current TP can't cover costs.",
    "ofi_against":       "OFI gate too strict; raise ofi_block_threshold or use OBI instead.",
    "depth_against":     "Depth gate too strict; raise depth_block_threshold.",
    "cooldown":          "Cooldown_*_s too long for the trading horizon.",
    "throttle":          "DecisionThrottle gap too large; lower min_seconds_between_entries_*.",
    "max_positions":     "Strategy is at max_positions — raise max_positions or capital.",
    "net_too_low":       "min_expected_net_profit_usd too high for the notional in use.",
    "rr_too_low":        "min_reward_risk_ratio floor too high; many trades fail RR check.",
    "spread":            "Wide spread observed at signal time — lift spread cap or wait.",
    "in_position":       "Strategy holding a position — increase max_positions to parallelise.",
    "pending":           "Pending order timeout too short / executor not filling — review exec.",
    "data_stale":        "Feed stale — check WS reconnects / data_feed_status latency.",
    "consec_loss":       "Loss streak triggered — review entry quality or extend cooldown_loss.",
    "daily_loss":        "Daily loss limit hit — raise max_daily_loss_usd or stop the strategy.",
    "min_size":          "Computed notional below the floor — raise it or skip this strategy.",
}


def _suggestion_for(main_reason: str) -> str:
    if not main_reason:
        return "—"
    head = main_reason.lower()
    for key, txt in _REASON_ACTION.items():
        if key in head:
            return txt
    return "Inspect manually — uncommon rejection reason."


# ── Reporting ───────────────────────────────────────────────────────────────

def _fmt_age(seconds: float) -> str:
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds/60:.1f}min"
    return f"{seconds/3600:.1f}h"


def build_report(args) -> str:
    repo = Path(__file__).resolve().parents[1]
    runtime = repo / "runtime"
    logs = repo / "logs"
    metrics = repo / "metrics_v9"

    cutoff = _resolve_since(args.since, runtime)
    now = time.time()
    span_min = (now - cutoff) / 60.0

    ecfg = _read_json(runtime / "engine_config.json") or {}
    feed = _read_json(runtime / "data_feed_status.json") or {}
    status = _read_json(runtime / "strategy_status.json") or []
    regime = _read_json(runtime / "regime_status.json") or {}

    all_dec = _read_csv(logs / "decisions_v9.csv")
    all_fills = _read_csv(logs / "fills_v9.csv")
    all_risk = _read_csv(logs / "risk_events.csv")

    dec = _filter_after(all_dec, ["timestamp"], cutoff)
    fills = _filter_after(all_fills, ["ts"], cutoff)
    risk = _filter_after(all_risk, ["ts", "timestamp"], cutoff)

    per_strat_dec, global_reasons = _aggregate_decisions(dec)
    per_strat_fills = _aggregate_fills(fills, cutoff)
    risk_counter = _aggregate_risk_events(risk)
    mqg_log = _scan_log_blocks(logs / "engine_v9.log", cutoff)

    out: list[str] = []
    out.append(f"# Trading Frequency Diagnostic\n")
    out.append(f"*Generated {_dt.datetime.now().isoformat(timespec='seconds')} — "
               f"window {span_min:.1f} min (since {_dt.datetime.fromtimestamp(cutoff).isoformat(timespec='seconds')})*\n")
    if ecfg:
        out.append(f"Engine session: PID **{ecfg.get('pid', '?')}**, "
                   f"config `{ecfg.get('config_path', '?')}`, "
                   f"session_id `{ecfg.get('session_id', '?')}`")

    # ── Feed health ─────────────────────────────────────────────────────────
    out.append("\n## 1. Data feed health\n")
    health = (feed or {}).get("data_feed_health") or {}
    per_sym = health.get("per_symbol") or {}
    if per_sym:
        out.append("| Coin | books/s | trades/s | last_book_age | last_trade_age | book_stale | mean_spread_bps |")
        out.append("|---|---|---|---|---|---|---|")
        for sym, s in sorted(per_sym.items()):
            out.append(f"| {sym} | {s.get('book_updates_per_sec', 0):.2f} | "
                       f"{s.get('trades_per_sec', 0):.2f} | "
                       f"{_fmt_age(abs(s.get('last_book_age_s') or 0))} | "
                       f"{_fmt_age(abs(s.get('last_trade_age_s') or 0))} | "
                       f"{'YES' if s.get('is_book_stale') else 'no'} | "
                       f"{s.get('spread_bps_mean', 0):.2f} |")
        out.append(f"\nGlobal: reconnections={health.get('reconnections', 0)}, "
                   f"queue_drops={health.get('queue_drops', 0)}, "
                   f"book_updates={health.get('book_updates_count', 0)}, "
                   f"trades={health.get('trade_events_count', 0)}")
    else:
        out.append("*(no data_feed_status.json — engine down or not writing feed health)*")

    # ── Per-strategy table ──────────────────────────────────────────────────
    out.append("\n## 2. Per-strategy summary\n")
    out.append("| Strategy | State | Raw signals | Accepted | Rejected | Main rejection | Trades/h | Warmup | Suggested action |")
    out.append("|---|---|---|---|---|---|---|---|---|")
    # Build a map name → status entry
    smap = {s.get("name"): s for s in status}
    all_names = sorted(set(list(per_strat_dec.keys())
                            + list(per_strat_fills.keys())
                            + list(smap.keys())) - {"(unattributed)"})
    for name in all_names:
        sd = per_strat_dec.get(name, {"PLACE": 0, "SKIP": 0, "FILTER_SKIP": 0,
                                       "reasons": collections.Counter()})
        sf = per_strat_fills.get(name, {"n": 0, "per_hour": 0.0})
        st = smap.get(name, {})
        raw = sd["PLACE"] + sd["SKIP"] + sd["FILTER_SKIP"]
        accepted = sf["n"]
        rejected = sd["SKIP"] + sd["FILTER_SKIP"]
        main_reason = sd["reasons"].most_common(1)
        main_reason_str = (main_reason[0][0] + f" ({main_reason[0][1]})") if main_reason else "—"
        warmup_pct = ""
        wstat = (st.get("warmup_status") or {})
        if wstat:
            for sym, tup in wstat.items():
                if isinstance(tup, dict) and "seconds" in tup:
                    cur, need, ok = tup["seconds"]
                    warmup_pct = f"{cur}/{need}s {'OK' if ok else ''}"
                    break
                if isinstance(tup, (list, tuple)) and len(tup) >= 2:
                    warmup_pct = f"{tup[0]}/{tup[1]}"
                    break
        if not warmup_pct:
            warmup_pct = st.get("state", "?")
        sug = _suggestion_for(main_reason[0][0] if main_reason else "")
        out.append(f"| {name} | {st.get('state', '?')} | {raw} | {accepted} | "
                   f"{rejected} | {main_reason_str} | {sf.get('per_hour', 0):.2f} | "
                   f"{warmup_pct} | {sug} |")

    # ── Top global rejection reasons ────────────────────────────────────────
    out.append("\n## 3. Top rejection reasons (all strategies, current session)\n")
    if global_reasons:
        out.append("| Reason | Count |\n|---|---|")
        for k, v in global_reasons.most_common(15):
            out.append(f"| `{k}` | {v} |")
    else:
        out.append("*(no rejected signals in window)*")

    # ── Gate breakdown ──────────────────────────────────────────────────────
    out.append("\n## 4. Gate breakdown\n")

    mqg_stats = feed.get("market_quality_gate_stats") or {}
    if mqg_stats:
        out.append("### MarketQualityGate (since process start)")
        out.append(f"- total evaluated : {mqg_stats.get('total_evaluated', 0)}")
        out.append(f"- total blocked   : {mqg_stats.get('total_blocked', 0)}")
        by = mqg_stats.get("blocks_by_reason") or {}
        if by:
            out.append("- top reasons :")
            for k, v in sorted(by.items(), key=lambda x: -x[1])[:8]:
                out.append(f"   - `{k}` : {v}")
    dt_stats = feed.get("decision_throttle_stats") or {}
    if dt_stats:
        out.append("\n### DecisionThrottle (since process start)")
        out.append(f"- total evaluated : {dt_stats.get('total_evaluated', 0)}")
        out.append(f"- total blocked   : {dt_stats.get('total_blocked', 0)}")
        by = dt_stats.get("blocks_by_reason") or {}
        if by:
            out.append("- top reasons :")
            for k, v in sorted(by.items(), key=lambda x: -x[1])[:5]:
                out.append(f"   - `{k}` : {v}")
    if risk_counter:
        out.append("\n### StrategyCapitalLedger / Portfolio (rejected events in window)")
        for k, v in risk_counter.most_common(8):
            out.append(f"- `{k}` : {v}")
    if mqg_log:
        out.append("\n### MQG blocks per (strategy, reason) — from engine_v9.log")
        top = sorted(mqg_log.items(), key=lambda x: -x[1])[:12]
        for (strat, reason), v in top:
            out.append(f"- {strat} → `{reason}` : {v}")

    # ── Strategies that never trade ─────────────────────────────────────────
    out.append("\n## 5. Strategies that never trade — likely cause\n")
    silent = []
    for name in all_names:
        sf = per_strat_fills.get(name, {"n": 0})
        if sf["n"] == 0:
            sd = per_strat_dec.get(name, {"reasons": collections.Counter(),
                                            "PLACE": 0, "SKIP": 0, "FILTER_SKIP": 0})
            st = smap.get(name, {})
            raw = sd["PLACE"] + sd["SKIP"] + sd["FILTER_SKIP"]
            if raw == 0 and st.get("state") == "ACTIVE":
                cause = "no signal emitted (likely warmup or never met entry condition)"
            elif sd["reasons"]:
                top = sd["reasons"].most_common(1)[0]
                cause = f"main rejection : `{top[0]}` ({top[1]} times)"
            elif st.get("state") != "ACTIVE":
                cause = f"state = {st.get('state')}"
            else:
                cause = "no recent data — check feed for its coins"
            silent.append((name, cause))
    if silent:
        out.append("| Strategy | Likely cause |")
        out.append("|---|---|")
        for name, cause in silent:
            out.append(f"| {name} | {cause} |")
    else:
        out.append("*(every active strategy has fired at least once)*")

    # ── Market regime ───────────────────────────────────────────────────────
    if regime:
        out.append("\n## 6. Market regime\n")
        out.append(f"- market regime (BTC) : **{regime.get('market_regime', '?')}**")
        out.append(f"- BTC 5-min return : {regime.get('btc_r_5m_pct', 0):.2f}%")
        per = regime.get("per_symbol") or {}
        non_normal = {k: v for k, v in per.items() if v != "NORMAL"}
        if non_normal:
            out.append(f"- non-normal coins : {non_normal}")
        adj = regime.get("active_adjustments") or []
        if adj:
            out.append(f"- active regime adjustments : {len(adj)}")
            for a in adj[:6]:
                out.append(f"   - {a['strategy']}.{a['param']}: {a['old']} → {a['new']} ({a['reason']})")

    # ── Headline counts ─────────────────────────────────────────────────────
    out.append("\n## 7. Headlines\n")
    total_signals = sum(d["PLACE"] + d["SKIP"] + d["FILTER_SKIP"]
                         for d in per_strat_dec.values())
    total_accepted = sum(d["PLACE"] for d in per_strat_dec.values())
    total_fills = sum(s["n"] for s in per_strat_fills.values())
    if span_min > 0:
        trades_per_2min = total_fills / (span_min / 2.0)
    else:
        trades_per_2min = 0
    out.append(f"- raw signals (window) : **{total_signals}**")
    out.append(f"- PLACE decisions      : **{total_accepted}**")
    out.append(f"- executed fills       : **{total_fills}**")
    out.append(f"- trades / 2min        : **{trades_per_2min:.2f}**")
    if total_fills:
        net = sum(s["net"] for s in per_strat_fills.values())
        wins = sum(s["wins"] for s in per_strat_fills.values())
        losses = sum(s["losses"] for s in per_strat_fills.values())
        wr = 100.0 * wins / max(total_fills, 1)
        out.append(f"- net PnL (window)     : **${net:+.4f}**  ({wins}W / {losses}L, "
                   f"{wr:.1f}% WR)")

    out.append("\n---\n*Generated by `scripts/diagnose_trading_frequency.py`.*")
    return "\n".join(out)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=0.0,
                    help="Watch for N minutes, then snapshot. 0 = snapshot now.")
    ap.add_argument("--since", default=None,
                    help="Epoch or ISO timestamp; only events after this. "
                         "Default = engine_config.json started_at.")
    ap.add_argument("--config", default=None,
                    help="(informational only) preset path of the running engine.")
    ap.add_argument("--out", default="reports/trading_frequency_diagnostic.md")
    args = ap.parse_args()

    if args.minutes > 0:
        print(f"Watching for {args.minutes:.1f} min...")
        time.sleep(args.minutes * 60)

    report = build_report(args)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    # Console preview (small, ASCII fallback).
    try:
        print(report)
    except UnicodeEncodeError:
        print(report.encode("ascii", "replace").decode("ascii"))
    print(f"\n[diagnose] rapport ecrit -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
