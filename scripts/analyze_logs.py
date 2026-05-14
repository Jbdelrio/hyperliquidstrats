"""
scripts/analyze_logs.py — Quick CLI summary of Artemisia v9 logs.

Reads logs/fills_v9.csv and logs/decisions_v9.csv and prints:
  - Trade aggregates: count, win rate, expectancy, profit factor, max DD
  - Top blocking reasons from decisions
  - PnL by strategy and by symbol
  - Trades per hour per strategy

Usage:
    python scripts/analyze_logs.py
    python scripts/analyze_logs.py --fills logs/fills_v9.csv --decisions logs/decisions_v9.csv

Designed to be safe (read-only) and dependency-light: stdlib only.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

# Allow running from repo root or scripts/
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtesting.data_loader import load_fills_as_trades
from backtesting.metrics import compute_metrics


_DEFAULT_FILLS    = "logs/fills_v9.csv"
_DEFAULT_DECISIONS = "logs/decisions_v9.csv"
_DEFAULT_ORDERS    = "logs/orders_v9.csv"


def _print_kv(title: str, kv: dict, fmt: str = "{:>10}") -> None:
    print(f"\n-- {title} --")
    if not kv:
        print("  (no data)")
        return
    for k, v in sorted(kv.items(), key=lambda it: -float(it[1])
                       if isinstance(it[1], (int, float)) else 0):
        if isinstance(v, (int, float)):
            print(f"  {k:<25} {fmt.format(v)}")
        else:
            print(f"  {k:<25} {v}")


def _blocking_reasons_from_decisions(path: str, top_n: int = 5) -> list[tuple[str, int]]:
    p = Path(path)
    if not p.exists():
        return []
    counts: Counter = Counter()
    with open(p, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            reason = (row.get("blocked_reason") or row.get("reason") or "").strip()
            if reason:
                counts[reason] += 1
    return counts.most_common(top_n)


def _trades_per_hour_per_strategy(trades: list[dict]) -> dict[str, float]:
    """Estimate trades/hour per strategy from min/max ts."""
    by_strat_ts: dict[str, list[float]] = defaultdict(list)
    for t in trades:
        s = str(t.get("strategy") or "")
        ts = float(t.get("ts") or 0)
        if s and ts > 0:
            by_strat_ts[s].append(ts)
    out: dict[str, float] = {}
    for s, ts_list in by_strat_ts.items():
        if len(ts_list) < 2:
            out[s] = 0.0
            continue
        span_h = (max(ts_list) - min(ts_list)) / 3600.0
        out[s] = round(len(ts_list) / span_h, 3) if span_h > 0 else 0.0
    return out


def _analyze_orders(path: str) -> dict:
    """Read orders_v9.csv and return aggregate stats. Empty dict if missing."""
    p = Path(path)
    if not p.exists():
        return {}
    out = {
        "total":       0,
        "filled":      0,
        "expired":     0,
        "cancelled":   0,
        "rejected":    0,
        "partial":     0,
        "maker":       0,
        "taker":       0,
        "slip_bps_sum": 0.0,
        "slip_bps_n":   0,
    }
    with open(p, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            out["total"] += 1
            status = (row.get("status") or "").upper()
            if status == "FILL":
                out["filled"] += 1
                slip = row.get("slippage_bps")
                try:
                    s = float(slip) if slip not in (None, "") else None
                except (TypeError, ValueError):
                    s = None
                if s is not None:
                    out["slip_bps_sum"] += s
                    out["slip_bps_n"]   += 1
            elif status == "EXPIRE":
                out["expired"] += 1
            elif status == "CANCEL":
                out["cancelled"] += 1
            elif status == "REJECT":
                out["rejected"] += 1
            elif status == "PARTIAL_FILL":
                out["partial"] += 1
            ot = (row.get("order_type") or "").upper()
            if ot == "MAKER_SIM":
                out["maker"] += 1
            elif ot == "TAKER_SIM":
                out["taker"] += 1
    return out


def main():
    ap = argparse.ArgumentParser(description="Analyze Artemisia v9 logs")
    ap.add_argument("--fills",     default=_DEFAULT_FILLS)
    ap.add_argument("--decisions", default=_DEFAULT_DECISIONS)
    ap.add_argument("--orders",    default=_DEFAULT_ORDERS)
    args = ap.parse_args()

    print("Artemisia v9 - Log Analysis")
    print(f"  fills:     {args.fills}")
    print(f"  decisions: {args.decisions}")

    trades = load_fills_as_trades(args.fills)
    if not trades:
        print(f"\nNo trades found in {args.fills}. Nothing to analyse.")
    metrics = compute_metrics(trades)

    print("\n== AGGREGATES ==")
    print(f"  Trades            : {metrics['n_trades']}")
    print(f"  Total PnL         : ${metrics['total_pnl']:+.2f}")
    print(f"  Win rate          : {metrics['win_rate']:.1f}%")
    print(f"  Expectancy/trade  : ${metrics['expectancy']:+.4f}")
    print(f"  Profit factor     : {metrics['profit_factor']:.2f}")
    print(f"  Max drawdown      : ${metrics['max_drawdown']:.2f}")
    print(f"  Sharpe (approx)   : {metrics['sharpe_approx']:.3f}")
    print(f"  Avg hold time     : {metrics['avg_hold_time_s']:.1f}s")
    print(f"  Trades/day        : {metrics['trades_per_day']:.2f}")
    print(f"  Avg win / loss    : ${metrics['avg_win']:+.4f}  /  ${metrics['avg_loss']:+.4f}")

    _print_kv("PnL by strategy", metrics["pnl_by_strategy"], fmt="${:+.4f}")
    _print_kv("PnL by symbol",   metrics["pnl_by_symbol"],   fmt="${:+.4f}")
    _print_kv("Exit reasons",    metrics["exit_reason_dist"], fmt="{:>10}")

    tph = _trades_per_hour_per_strategy(trades)
    _print_kv("Trades / hour by strategy", tph, fmt="{:>10.3f}")

    blocking = _blocking_reasons_from_decisions(args.decisions, top_n=5)
    print("\n-- Top blocking reasons (from decisions log) --")
    if not blocking:
        print("  (no blocking-reason data found)")
    for reason, cnt in blocking:
        print(f"  {cnt:>6}  {reason}")

    # ── Orders log (Phase 6) ─────────────────────────────────────────
    orders = _analyze_orders(args.orders)
    print("\n-- Orders log stats --")
    if not orders:
        print("  (no orders_v9.csv yet)")
    else:
        n = orders["total"]
        print(f"  total                {n}")
        print(f"  filled               {orders['filled']}")
        print(f"  expired              {orders['expired']}")
        print(f"  cancelled            {orders['cancelled']}")
        if orders["rejected"]: print(f"  rejected             {orders['rejected']}")
        if orders["partial"]:  print(f"  partial              {orders['partial']}")
        if n > 0:
            expire_rate = orders["expired"] / n * 100
            missed_fill = (orders["expired"] + orders["cancelled"]) / n * 100
            print(f"  expired rate         {expire_rate:.1f}%")
            print(f"  missed-fill rate     {missed_fill:.1f}%")
        if orders["slip_bps_n"] > 0:
            avg_slip = orders["slip_bps_sum"] / orders["slip_bps_n"]
            print(f"  avg slippage_bps     {avg_slip:.2f}")
        print(f"  maker fills          {orders['maker']}")
        print(f"  taker fills          {orders['taker']}")


if __name__ == "__main__":
    main()
