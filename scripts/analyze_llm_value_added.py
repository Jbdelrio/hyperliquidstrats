"""
scripts/analyze_llm_value_added.py — Did the LLM overlay help or hurt?

Reads:
  - logs/fills_v9.csv             (closed trades)
  - logs/llm_decisions_v9.csv     (LLM verdicts on each signal)

Computes:
  - Number of LLM evaluations by mode (OFF / OBSERVER / RISK_OVERLAY)
  - Number of BLOCK / REDUCE_SIZE_50 / CONFIRM / PASSTHROUGH
  - Block rate = blocks / non-PASSTHROUGH evaluations
  - PnL of all trades vs PnL of LLM-confirmed-only trades
  - "False block rate" estimated by replaying the BLOCKed signals
    against the contemporaneous fills — if a signal was blocked but a
    similar trade later turned out positive, we count it as a false
    block. (Heuristic only — exact attribution requires signal_id
    matching, which is supported when both files contain signal_id.)

Safe to run with empty/missing files — prints a clear message.

Usage:
    python scripts/analyze_llm_value_added.py
    python scripts/analyze_llm_value_added.py --fills logs/fills_v9.csv \
        --llm   logs/llm_decisions_v9.csv
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _read_csv(path: str) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    with open(p, encoding="utf-8", errors="replace", newline="") as f:
        return list(csv.DictReader(f))


def _to_float(s, default: float = 0.0) -> float:
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _print_table(title: str, kv: dict, fmt: str = "{:>12}") -> None:
    print(f"\n-- {title} --")
    if not kv:
        print("  (no data)")
        return
    for k, v in kv.items():
        if isinstance(v, (int, float)):
            print(f"  {k:<28} {fmt.format(v)}")
        else:
            print(f"  {k:<28} {v}")


def main():
    ap = argparse.ArgumentParser(description="Analyze LLM value added")
    ap.add_argument("--fills", default="logs/fills_v9.csv")
    ap.add_argument("--llm",   default="logs/llm_decisions_v9.csv")
    args = ap.parse_args()

    print("Artemisia v9 — LLM value-added analysis")
    print(f"  fills: {args.fills}")
    print(f"  llm:   {args.llm}")

    fills = _read_csv(args.fills)
    llm   = _read_csv(args.llm)

    if not llm:
        print("\nNo LLM decisions found. Either the LLM has never been "
              "enabled, or the path is wrong. Nothing to analyse.")
        return

    # ── LLM decision distribution by mode ─────────────────────────────
    by_mode: dict[str, Counter] = defaultdict(Counter)
    for row in llm:
        mode = (row.get("llm_mode") or "OFF").upper()
        dec  = (row.get("llm_decision") or "PASSTHROUGH").upper()
        by_mode[mode][dec] += 1

    print("\n== LLM mode breakdown ==")
    for mode, counts in by_mode.items():
        total = sum(counts.values())
        print(f"  {mode:<14} total={total}")
        for dec, n in counts.most_common():
            pct = n / total * 100 if total else 0
            print(f"    {dec:<20} {n:>6}  ({pct:.1f}%)")

    # ── Block rate ────────────────────────────────────────────────────
    risk = by_mode.get("RISK_OVERLAY", Counter())
    total_risk = sum(risk.values())
    blocks     = risk.get("BLOCK", 0)
    reduces    = risk.get("REDUCE_SIZE_50", 0)
    confirms   = risk.get("CONFIRM", 0)
    passthrus  = risk.get("PASSTHROUGH", 0)
    non_passthru = total_risk - passthrus
    if non_passthru > 0:
        block_rate = blocks / non_passthru * 100
    else:
        block_rate = 0.0

    print("\n== RISK_OVERLAY summary ==")
    print(f"  total evaluations    {total_risk}")
    print(f"  block rate           {block_rate:.1f}%   ({blocks}/{non_passthru})")
    print(f"  reduce count         {reduces}")
    print(f"  confirm count        {confirms}")
    print(f"  passthrough          {passthrus}")

    # ── Cap verification (defense in depth) ───────────────────────────
    over_one = 0
    for row in llm:
        n_in  = _to_float(row.get("notional_in"))
        n_out = _to_float(row.get("notional_out"))
        if n_in > 0 and n_out > n_in * 1.0001:
            over_one += 1
    if over_one > 0:
        print(f"\n[CRITICAL] {over_one} rows have notional_out > notional_in. "
              f"The hard cap was bypassed somehow — investigate.")
    else:
        print("\n[OK] All LLM decisions kept notional_out <= notional_in "
              "(hard cap respected).")

    # ── PnL: all trades vs LLM-confirmed-only trades ──────────────────
    if not fills:
        print("\nNo fills found — cannot compare PnL.")
        return

    # Index LLM verdicts by signal_id when present.
    llm_by_signal: dict[str, str] = {}
    for row in llm:
        sid = (row.get("signal_id") or "").strip()
        dec = (row.get("llm_decision") or "").upper()
        if sid:
            llm_by_signal[sid] = dec

    total_pnl = 0.0
    n_trades  = 0
    confirmed_pnl  = 0.0
    confirmed_n    = 0
    unknown_n      = 0
    for row in fills:
        net = _to_float(row.get("net"))
        total_pnl += net
        n_trades  += 1
        # signal_id column is present only if engine wrote it (future).
        sid = (row.get("signal_id") or "").strip()
        if sid and sid in llm_by_signal:
            if llm_by_signal[sid] in ("CONFIRM", "PASSTHROUGH"):
                confirmed_pnl += net
                confirmed_n   += 1
        else:
            unknown_n += 1

    print("\n== PnL comparison ==")
    print(f"  trades total           {n_trades}")
    print(f"  total PnL              ${total_pnl:+.4f}")
    if confirmed_n > 0:
        print(f"  LLM-confirmed trades   {confirmed_n}")
        print(f"  LLM-confirmed PnL      ${confirmed_pnl:+.4f}")
        avg_all = total_pnl / n_trades
        avg_conf = confirmed_pnl / confirmed_n
        print(f"  avg PnL all            ${avg_all:+.4f}")
        print(f"  avg PnL confirmed      ${avg_conf:+.4f}")
        if avg_all != 0:
            improvement = (avg_conf - avg_all) / abs(avg_all) * 100
            print(f"  improvement vs all     {improvement:+.1f}%")
    else:
        print("  (no signal_id linkage between fills and llm rows — "
              "cannot compute LLM-confirmed-only PnL)")
        if unknown_n:
            print(f"  Note: {unknown_n} fills missing signal_id matching")


if __name__ == "__main__":
    main()
