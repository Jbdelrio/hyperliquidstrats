"""
analyze_decisions.py — CLI analysis of S8 EMS decision log.

Usage:
    python scripts/analyze_decisions.py [logs/decisions_v9.csv] [--spread 2.0] [--hours 2.0]

Reports why the bot skips opportunities: decision type breakdown, top blocking
reasons, per-coin stats, and a what-if simulation for relaxing min_spread_bps.
"""
import csv
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Optional


# ── CSV loader ──────────────────────────────────────────────────────────────────

def load_decisions(path: str) -> list:
    """Load decisions CSV.  Returns empty list if file missing."""
    p = Path(path)
    if not p.exists():
        return []
    rows = []
    with open(p, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            for col in ("spread_bps", "hurst", "har_rv_forecast",
                        "kalman_fv", "obi", "mid",
                        "buy_price", "sell_price", "size", "notional_usd"):
                try:
                    v = row.get(col, "")
                    row[col] = float(v) if v not in ("", "None", None) else None
                except (ValueError, TypeError):
                    row[col] = None
            rows.append(row)
    return rows


# ── Summary report ──────────────────────────────────────────────────────────────

def report_summary(rows: list, last_hours: float = 2.0) -> None:
    cutoff = time.time() - last_hours * 3600
    recent = [r for r in rows if _ts(r) >= cutoff]

    total = len(recent)
    if total == 0:
        print(f"No decisions in the last {last_hours:.0f}h — run the bot first.")
        return

    by_type: Counter = Counter(r["decision"] for r in recent)
    print(f"\n{'='*62}")
    print(f"  S8 EMS — Decision analysis  (last {last_hours:.0f}h, {total} decisions)")
    print(f"{'='*62}")
    print(f"  PLACE   : {by_type.get('PLACE', 0):6d}  "
          f"({100 * by_type.get('PLACE', 0) / total:5.1f}%)")
    print(f"  SKIP    : {by_type.get('SKIP',  0):6d}  "
          f"({100 * by_type.get('SKIP',  0) / total:5.1f}%)")

    skips = [r for r in recent if r["decision"] == "SKIP"]
    if not skips:
        print("\n  No skips — all decisions placed quotes!")
        return

    print(f"\n  Top skip reasons  (n={len(skips)}):")
    reasons = Counter(r["reason"] for r in skips)
    for reason, cnt in reasons.most_common(10):
        pct = 100 * cnt / len(skips)
        bar = "█" * int(pct / 2)
        print(f"    {reason:<32} {cnt:5d}  {pct:5.1f}%  {bar}")


def report_per_coin(rows: list, last_hours: float = 2.0) -> None:
    cutoff = time.time() - last_hours * 3600
    recent = [r for r in rows if _ts(r) >= cutoff]
    if not recent:
        return

    coins = sorted({r["symbol"] for r in recent})
    print(f"\n  Per-coin breakdown:")
    print(f"  {'Coin':<8} {'Total':>7} {'PLACE%':>8} {'SKIP%':>7}  Top skip reason")
    print(f"  {'-'*72}")
    for coin in coins:
        coin_rows = [r for r in recent if r["symbol"] == coin]
        n = len(coin_rows)
        place_n = sum(1 for r in coin_rows if r["decision"] == "PLACE")
        skip_n  = sum(1 for r in coin_rows if r["decision"] == "SKIP")
        skip_reasons = Counter(r["reason"] for r in coin_rows if r["decision"] == "SKIP")
        top = skip_reasons.most_common(1)[0][0] if skip_reasons else "—"
        print(f"  {coin:<8} {n:>7d}  {100*place_n/n:>7.1f}%  "
              f"{100*skip_n/n:>6.1f}%  {top}")


# ── What-if ─────────────────────────────────────────────────────────────────────

def compute_what_if(rows: list, new_min_spread_bps: float,
                    last_hours: float = 2.0) -> dict:
    """Pure computation (no printing).  Returns result dict."""
    cutoff = time.time() - last_hours * 3600
    recent = [r for r in rows if _ts(r) >= cutoff]
    total_skips = sum(1 for r in recent if r["decision"] == "SKIP")
    spread_skips = [
        r for r in recent
        if r["decision"] == "SKIP"
        and r["reason"] == "spread_too_tight"
        and r.get("spread_bps") is not None
        and r["spread_bps"] >= new_min_spread_bps
    ]
    recovered = len(spread_skips)
    pct = 100.0 * recovered / total_skips if total_skips > 0 else 0.0
    avg_spread: Optional[float] = (
        sum(r["spread_bps"] for r in spread_skips) / recovered
        if recovered > 0 else None
    )
    return {
        "recovered_skips": recovered,
        "total_skips":     total_skips,
        "pct_recovered":   pct,
        "avg_spread_bps":  avg_spread,
    }


def report_what_if(rows: list, new_min_spread_bps: float,
                   last_hours: float = 2.0) -> dict:
    result = compute_what_if(rows, new_min_spread_bps, last_hours)
    print(f"\n  What-if: min_spread_bps = {new_min_spread_bps:.1f} bps")
    print(f"    Recoverable spread_too_tight skips : "
          f"{result['recovered_skips']} / {result['total_skips']} "
          f"({result['pct_recovered']:.1f}%)")
    if result["avg_spread_bps"] is not None:
        print(f"    Average spread of recovered skips  : "
              f"{result['avg_spread_bps']:.2f} bps")
    return result


# ── Internal helpers ─────────────────────────────────────────────────────────────

def _ts(row: dict) -> float:
    try:
        return float(row["timestamp"])
    except (KeyError, ValueError, TypeError):
        return 0.0


# ── CLI entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Analyze S8 EMS decision log")
    parser.add_argument("csv_path", nargs="?",
                        default="logs/decisions_v9.csv",
                        help="Path to decisions CSV  (default: logs/decisions_v9.csv)")
    parser.add_argument("--spread", type=float, default=2.0,
                        help="What-if min_spread_bps threshold  (default: 2.0)")
    parser.add_argument("--hours", type=float, default=2.0,
                        help="Analyse last N hours  (default: 2.0)")
    args = parser.parse_args()

    rows = load_decisions(args.csv_path)
    if not rows:
        print(f"No data found at {args.csv_path}")
        sys.exit(0)

    report_summary(rows, last_hours=args.hours)
    report_per_coin(rows, last_hours=args.hours)
    report_what_if(rows, new_min_spread_bps=args.spread, last_hours=args.hours)
    print()


if __name__ == "__main__":
    main()
