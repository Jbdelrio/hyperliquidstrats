#!/usr/bin/env python
"""
audit_data_feed.py — Connect to the Hyperliquid WebSocket for N minutes
and report on data feed quality.

Usage :
    python scripts/audit_data_feed.py --minutes 5 --coins BTC,ETH,SOL,HYPE

Exit code :
    0  if all critical checks pass
    1  if at least one critical check fails

Writes a Markdown report to `reports/data_feed_audit.md`.
"""
from __future__ import annotations

import argparse
import asyncio
import math
import sys
import time
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.orderbook_manager import OrderbookManager  # noqa: E402


# Critical-condition thresholds — overridable via CLI later.
_MAX_SPREAD_BPS = {"BTC": 5.0, "ETH": 5.0, "SOL": 8.0, "DEFAULT": 15.0}
_MAX_LATENCY_P95_MS = 1000.0
_MAX_STALE_BOOK_S = 10.0


def _parse_args():
    p = argparse.ArgumentParser(description="Audit Hyperliquid data feed quality.")
    p.add_argument("--minutes", type=float, default=5.0,
                   help="Audit duration (minutes).")
    p.add_argument("--coins", default="BTC,ETH,SOL,HYPE",
                   help="Comma-separated symbol list.")
    p.add_argument("--out", default="reports/data_feed_audit.md")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def _max_spread(sym: str) -> float:
    return _MAX_SPREAD_BPS.get(sym, _MAX_SPREAD_BPS["DEFAULT"])


def _format(v, digits=2):
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "n/a"
    return f"{v:.{digits}f}"


async def _run(coins: list[str], duration_s: float, quiet: bool) -> dict:
    obm = OrderbookManager(coins)
    await obm.connect()
    deadline = time.time() + duration_s
    last_print = 0.0
    try:
        while time.time() < deadline:
            await asyncio.sleep(2.0)
            now = time.time()
            if not quiet and (now - last_print) >= 30.0:
                snap = obm.health_snapshot()
                ps = snap.get("per_symbol", {})
                print(f"\n— t-{int(deadline - now):>4}s — drops={snap['queue_drops']} | "
                      f"reconnects={snap['reconnections']} | invalid={snap['invalid_book_count']} | "
                      f"crossed={snap['crossed_book_count']}")
                for sym, st in ps.items():
                    print(f"   {sym:<4} bu/s={_format(st['book_updates_per_sec'])} "
                          f"tr/s={_format(st['trades_per_sec'])} "
                          f"spread={_format(st['spread_bps_mean'])}bps "
                          f"lat_p95={_format(st['p95_latency_ms'])}ms "
                          f"book_age={_format(st['last_book_age_s'])}s")
                last_print = now
    finally:
        snap = obm.health_snapshot()
        await obm.stop()
    return snap


def _evaluate(snap: dict) -> tuple[bool, list[str]]:
    """Return (ok, critical_messages)."""
    crit = []
    if snap["queue_drops"] > 0:
        crit.append(f"queue drops detected: {snap['queue_drops']}")
    if snap["crossed_book_count"] > 0:
        crit.append(f"crossed books detected: {snap['crossed_book_count']}")
    for sym, st in snap["per_symbol"].items():
        # Core liquids must have continuous data.
        if sym in ("BTC", "ETH", "SOL"):
            if st["last_book_age_s"] > _MAX_STALE_BOOK_S:
                crit.append(f"{sym}: book stale {st['last_book_age_s']:.1f}s > {_MAX_STALE_BOOK_S}")
            if st["trade_events"] == 0:
                crit.append(f"{sym}: zero trade events during the whole window")
        lat = st["p95_latency_ms"]
        if lat is not None and math.isfinite(lat) and lat > _MAX_LATENCY_P95_MS:
            crit.append(f"{sym}: latency p95 {lat:.0f}ms > {_MAX_LATENCY_P95_MS:.0f}")
        spread = st["spread_bps_mean"]
        if spread is not None and math.isfinite(spread):
            cap = _max_spread(sym)
            if spread > cap:
                crit.append(f"{sym}: mean spread {spread:.2f}bps > {cap:.2f}")
    return (len(crit) == 0), crit


def _write_report(snap: dict, ok: bool, crit: list[str], out_path: Path) -> None:
    lines = []
    status = "DATA FEED OK" if ok else "DATA FEED NOT OK"
    lines.append(f"# Hyperliquid Data Feed Audit — {status}")
    lines.append("")
    lines.append(f"- Snapshot ts : {time.strftime('%Y-%m-%dT%H:%M:%S', time.gmtime(snap.get('ts', time.time())))}")
    lines.append(f"- Symbols     : {', '.join(snap.get('symbols', []))}")
    lines.append(f"- Reconnections : {snap.get('reconnections', 0)}")
    lines.append(f"- JSON parse errors : {snap.get('json_parse_errors_count', 0)}")
    lines.append(f"- Invalid books : {snap.get('invalid_book_count', 0)}")
    lines.append(f"- Crossed books : {snap.get('crossed_book_count', 0)}")
    lines.append(f"- Queue drops (book) : {snap.get('dropped_book_updates_count', 0)}")
    lines.append(f"- Queue drops (trade) : {snap.get('dropped_trade_events_count', 0)}")
    lines.append("")
    lines.append("## Per-symbol")
    lines.append("")
    lines.append("| Symbol | bu/s | tr/s | spread_mean | spread_p95 | spread_max | lat_mean | lat_p95 | book_age_s | trade_age_s | invalid | crossed |")
    lines.append("|--------|------|------|-------------|-----------|-----------|----------|--------|------------|------------|---------|---------|")
    for sym, st in snap.get("per_symbol", {}).items():
        lines.append("| {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} | {} |".format(
            sym,
            _format(st["book_updates_per_sec"]),
            _format(st["trades_per_sec"]),
            _format(st["spread_bps_mean"]),
            _format(st["spread_bps_p95"]),
            _format(st["spread_bps_max"]),
            _format(st["avg_latency_ms"]),
            _format(st["p95_latency_ms"]),
            _format(st["last_book_age_s"]),
            _format(st["last_trade_age_s"]),
            st["invalid_books"],
            st["crossed_books"],
        ))
    lines.append("")
    if crit:
        lines.append("## Critical issues")
        for c in crit:
            lines.append(f"- {c}")
        lines.append("")
    lines.append("## Recommendation")
    if ok:
        lines.append("Data feed quality is acceptable — paper trading can proceed.")
    else:
        lines.append("Do **NOT** start paper trading until the critical issues above are resolved.")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    args = _parse_args()
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    duration_s = max(30.0, args.minutes * 60.0)
    print(f"Auditing {coins} for {duration_s/60:.1f} min ...")
    snap = asyncio.run(_run(coins, duration_s, args.quiet))
    ok, crit = _evaluate(snap)
    _write_report(snap, ok, crit, Path(args.out))
    print(f"\nWrote {args.out}")
    if ok:
        print("DATA FEED OK")
        return 0
    print("DATA FEED NOT OK:")
    for c in crit:
        print(f"  - {c}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
