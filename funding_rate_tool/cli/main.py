"""CLI entry point — `python -m cli.main --coins BTC,ETH --arbitrage`."""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

from cli import cache, display
from cli.fetcher import (
    fetch_all, fetch_detail, fetch_history, fetch_many, fetch_top_n,
)
from config.endpoints import (
    BULK_EXCHANGES, DETAIL_EXCHANGES, EXCHANGES, HISTORY_EXCHANGES,
)
from config.settings import DEFAULT_COINS, DEFAULT_EXCHANGES
from utils.helpers import calculate_arbitrage


def _parse_list(value: str, allowed: set | None = None) -> list[str]:
    items = [v.strip().lower() for v in value.split(",") if v.strip()]
    if "all" in items and allowed:
        return sorted(allowed)
    if allowed:
        items = [i for i in items if i in allowed]
    return items


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="funding-rate-tool",
        description="Multi-exchange funding-rate fetcher with arbitrage detection.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    p.add_argument("--coins", default=",".join(DEFAULT_COINS),
                   help=f"Comma-separated coins (default: {','.join(DEFAULT_COINS)})")
    p.add_argument("--exchanges", default="all",
                   help=f"Comma-separated exchanges or 'all'. Available: {','.join(EXCHANGES)}")
    p.add_argument("--compare", action="store_true",
                   help="Show per-coin comparison table (vs avg/min/max).")
    p.add_argument("--arbitrage", action="store_true",
                   help="Show arbitrage opportunities across exchanges.")
    p.add_argument("--alert", type=float, default=None,
                   help="Spread threshold (decimal, e.g. 0.0001 = 1 bps) — exits non-zero if hit.")
    p.add_argument("--export", choices=["csv", "json"], default=None,
                   help="Export raw results to a file (use --output to set path).")
    p.add_argument("--output", default=None,
                   help="Output path for --export (default: ./funding_rates.<ext>).")
    p.add_argument("--no-cache", action="store_true",
                   help="Skip the SQLite cache and force fresh requests.")
    p.add_argument("--clear-cache", action="store_true",
                   help="Drop all cached entries before running.")
    p.add_argument("--watch", type=int, default=None, metavar="SECONDS",
                   help="Refresh continuously every N seconds (Ctrl+C to stop).")
    p.add_argument("--top", type=int, default=None, metavar="N",
                   help=f"Top-N highest |rate| on --exchange (supported: "
                        f"{','.join(BULK_EXCHANGES)}).")
    p.add_argument("--lowest", type=int, default=None, metavar="N",
                   help="Alias for --top N --top-mode low (most negative rates).")
    p.add_argument("--all", action="store_true",
                   help="Dump ALL perps for --exchange (no N cap).")
    p.add_argument("--top-mode", default="abs", choices=["abs", "high", "low"],
                   help="abs=most extreme, high=most positive, low=most negative.")

    # Single-exchange operations
    p.add_argument("--exchange", default=None, choices=sorted(EXCHANGES.keys()),
                   help="Exchange for --symbol / --detail / --history / --top / --all "
                        "(default: binance).")
    p.add_argument("--top-exchange", default=None,
                   choices=sorted(BULK_EXCHANGES.keys()),
                   help="DEPRECATED: alias for --exchange.")
    p.add_argument("--symbol", default=None,
                   help="Raw exchange symbol (e.g. BTCUSDT, BTC-USDT-SWAP, XBTUSDT). "
                        "Bypasses the coin->symbol mapping.")
    p.add_argument("--detail", action="store_true",
                   help="Rich detail panel for --symbol (rate, mark, index, OI, vol, change %%).")
    p.add_argument("--history", type=int, default=None, metavar="N",
                   help="Last N historical fundings for --symbol on --exchange.")
    return p


async def _run_once(args: argparse.Namespace) -> int:
    # Resolve which exchange to target for single-exchange ops.
    exchange = args.exchange or args.top_exchange or "binance"

    # --- Detail panel for one symbol ---------------------------------------
    if args.detail:
        if not args.symbol:
            print("error: --detail requires --symbol", file=sys.stderr)
            return 2
        if exchange not in DETAIL_EXCHANGES:
            print(f"error: --detail not supported for {exchange}", file=sys.stderr)
            return 2
        d = await fetch_detail(exchange, args.symbol)
        display.display_detail(d)
        if args.export and d:
            out = Path(args.output) if args.output else Path(
                f"detail_{exchange}_{args.symbol}.{args.export}")
            display.export_results([d], out, args.export)
        return 0 if d else 1

    # --- History of one symbol --------------------------------------------
    if args.history:
        if not args.symbol:
            print("error: --history requires --symbol", file=sys.stderr)
            return 2
        if exchange not in HISTORY_EXCHANGES:
            print(f"error: --history not supported for {exchange}", file=sys.stderr)
            return 2
        rows = await fetch_history(exchange, args.symbol, args.history)
        display.display_history(rows, exchange, args.symbol)
        if args.export and rows:
            out = Path(args.output) if args.output else Path(
                f"history_{exchange}_{args.symbol}.{args.export}")
            display.export_results(rows, out, args.export)
        return 0 if rows else 1

    # --- Raw symbol lookup (current rate only) -----------------------------
    if args.symbol:
        d = await fetch_detail(exchange, args.symbol)  # rich is cheap, use it
        display.display_detail(d)
        return 0 if d else 1

    # --- All perps for one exchange ---------------------------------------
    if args.all:
        if exchange not in BULK_EXCHANGES:
            print(f"error: --all not supported for {exchange}", file=sys.stderr)
            return 2
        rows = await fetch_all(exchange, use_cache=not args.no_cache)
        display.display_top_n(rows, exchange, "high")  # sorted descending
        if args.export and rows:
            out = Path(args.output) if args.output else Path(
                f"all_{exchange}.{args.export}")
            display.export_results(rows, out, args.export)
        return 0

    # --- --lowest is sugar for --top --top-mode low -----------------------
    if args.lowest:
        args.top = args.lowest
        args.top_mode = "low"

    # --- Top-N standalone mode -------------------------------------------
    if args.top:
        if exchange not in BULK_EXCHANGES:
            print(f"error: --top not supported for {exchange}", file=sys.stderr)
            return 2
        rows = await fetch_top_n(exchange, n=args.top, mode=args.top_mode,
                                 use_cache=not args.no_cache)
        display.display_top_n(rows, exchange, args.top_mode)
        if args.export:
            out = Path(args.output) if args.output else Path(
                f"top_{exchange}.{args.export}")
            display.export_results(rows, out, args.export)
        return 0

    coins = [c.upper() for c in _parse_list(args.coins, allowed=None)]
    exchanges = _parse_list(args.exchanges, allowed=set(EXCHANGES.keys()))
    if not exchanges:
        exchanges = DEFAULT_EXCHANGES

    if not coins:
        print("error: no coins specified", file=sys.stderr)
        return 2

    results = await fetch_many(coins, exchanges, use_cache=not args.no_cache)

    display.display_current(results)
    if args.compare:
        display.display_comparison(results)
    if args.arbitrage or args.alert is not None:
        display.display_arbitrage(results, alert_threshold=args.alert)

    if args.export:
        out = Path(args.output) if args.output else Path(f"funding_rates.{args.export}")
        display.export_results(results, out, args.export)

    # Exit code: non-zero if --alert was provided and any spread hit it
    if args.alert is not None:
        opps = calculate_arbitrage(results)
        if any(o["spread"] >= args.alert for o in opps):
            print(f"\n[!] spread >= {args.alert} hit (exit code 10)", file=sys.stderr)
            return 10
    return 0


async def _run_watch(args: argparse.Namespace) -> int:
    print(f"Watching every {args.watch}s — Ctrl+C to stop.")
    try:
        while True:
            print("\033[2J\033[H", end="")  # ANSI clear screen
            print(f"--- refresh @ {time.strftime('%H:%M:%S')} ---")
            await _run_once(args)
            await asyncio.sleep(args.watch)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.clear_cache:
        n = cache.clear()
        print(f"Cleared {n} cached entries.")

    if args.watch:
        return asyncio.run(_run_watch(args))
    return asyncio.run(_run_once(args))


if __name__ == "__main__":
    sys.exit(main())
