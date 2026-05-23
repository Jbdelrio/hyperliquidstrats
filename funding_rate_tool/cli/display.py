"""Terminal display — colored tables for current / compare / arbitrage modes."""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional

try:
    from colorama import Fore, Style, init as colorama_init
    colorama_init()
    GREEN, RED, DIM, RESET, BOLD, CYAN, YELLOW = (
        Fore.GREEN, Fore.RED, Style.DIM, Style.RESET_ALL, Style.BRIGHT, Fore.CYAN, Fore.YELLOW,
    )
except ImportError:  # graceful fallback if colorama not installed
    GREEN = RED = DIM = RESET = BOLD = CYAN = YELLOW = ""

from config.endpoints import FLAKY
from utils.helpers import (
    calculate_arbitrage,
    calculate_comparison,
    format_utc_ms,
    rate_to_annualized_pct,
    rate_to_bps,
    time_until,
)


def _fmt(v, suffix="", n=4):
    return f"{v:,.{n}f}{suffix}" if v is not None else "-"


def _rate_color(rate: float) -> str:
    if rate > 0:
        return GREEN
    if rate < 0:
        return RED
    return DIM


def _exchange_label(ex: str) -> str:
    name = ex.upper()
    return f"{name}*" if ex in FLAKY else name


def display_current(results: List[Dict]) -> None:
    if not results:
        print(f"{RED}No funding rates retrieved.{RESET}")
        return

    results = sorted(results, key=lambda x: (x["coin"], x["exchange"]))
    print()
    print(f"{BOLD}{CYAN}CURRENT FUNDING RATES (UTC){RESET}")
    print("-" * 92)
    print(f"{'Coin':<6} | {'Exchange':<13} | {'Rate':>11} | {'BPS':>8} | {'APR%':>8} | {'Next Funding':<22}")
    print("-" * 92)

    for r in results:
        color = _rate_color(r["rate"])
        marker = "+" if r["rate"] > 0 else ("-" if r["rate"] < 0 else " ")
        next_str = format_utc_ms(r["next_time_ms"])
        print(
            f"{r['coin']:<6} | {_exchange_label(r['exchange']):<13} | "
            f"{color}{marker}{abs(r['rate']):>10.6f}{RESET} | "
            f"{color}{rate_to_bps(r['rate']):>+8.2f}{RESET} | "
            f"{color}{rate_to_annualized_pct(r['rate'], r['exchange']):>+8.2f}{RESET} | "
            f"{next_str}"
        )
    print("-" * 92)
    if any(r["exchange"] in FLAKY for r in results):
        print(f"{DIM}(*) marked exchange is occasionally unavailable.{RESET}")


def display_comparison(results: List[Dict]) -> None:
    comp = calculate_comparison(results)
    if not comp:
        print(f"{RED}Nothing to compare.{RESET}")
        return

    for coin, info in comp.items():
        print()
        print(f"{BOLD}{CYAN}{coin}{RESET}  "
              f"avg={info['avg']*10000:+.2f} bps  "
              f"spread={info['spread']*10000:.2f} bps")
        print("-" * 80)
        print(f"{'Exchange':<13} | {'Rate':>11} | {'BPS':>8} | {'vs Avg':>10} | {'vs Min':>10} | {'vs Max':>10}")
        print("-" * 80)
        for row in info["rows"]:
            color = _rate_color(row["rate"])
            vs_avg = (row["rate"] - info["avg"]) * 10000
            vs_min = (row["rate"] - info["min"]) * 10000
            vs_max = (row["rate"] - info["max"]) * 10000
            print(
                f"{_exchange_label(row['exchange']):<13} | "
                f"{color}{row['rate']:>+11.6f}{RESET} | "
                f"{color}{rate_to_bps(row['rate']):>+8.2f}{RESET} | "
                f"{vs_avg:>+10.2f} | {vs_min:>+10.2f} | {vs_max:>+10.2f}"
            )


def display_arbitrage(results: List[Dict], alert_threshold: Optional[float] = None) -> None:
    opps = calculate_arbitrage(results)
    if not opps:
        print(f"{RED}No arbitrage opportunities (need ≥2 exchanges per coin).{RESET}")
        return

    print()
    print(f"{BOLD}{CYAN}ARBITRAGE OPPORTUNITIES{RESET}  (long low-rate, short high-rate)")
    print("-" * 92)
    print(f"{'Coin':<6} | {'Long':<12} | {'Short':<12} | {'Spread bps':>11} | "
          f"{'Long rate':>11} | {'Short rate':>11} | {'Alert':<6}")
    print("-" * 92)
    for o in opps:
        spread_color = GREEN if o["spread_bps"] >= (alert_threshold or 0) * 10000 else ""
        alert = ""
        if alert_threshold is not None and o["spread"] >= alert_threshold:
            alert = f"{YELLOW}{BOLD}HIT{RESET}"
        print(
            f"{o['coin']:<6} | {_exchange_label(o['long_exchange']):<12} | "
            f"{_exchange_label(o['short_exchange']):<12} | "
            f"{spread_color}{o['spread_bps']:>+11.2f}{RESET} | "
            f"{o['long_rate']:>+11.6f} | {o['short_rate']:>+11.6f} | {alert:<6}"
        )
    print("-" * 92)


def display_top_n(rows: List[Dict], exchange: str, mode: str) -> None:
    if not rows:
        print(f"{RED}No data returned for {exchange.upper()}.{RESET}")
        return
    mode_label = {"abs": "most extreme", "high": "highest", "low": "lowest"}.get(mode, mode)
    print()
    print(f"{BOLD}{CYAN}TOP {len(rows)} {mode_label} funding rates on {exchange.upper()}{RESET}")
    print("-" * 80)
    print(f"{'#':<3} | {'Symbol':<20} | {'Rate':>11} | {'BPS':>10} | {'APR %':>9} | {'Next funding':<20}")
    print("-" * 80)
    for i, r in enumerate(rows, 1):
        color = _rate_color(r["rate"])
        symbol = r.get("symbol") or r["coin"]
        print(
            f"{i:<3} | {symbol:<20} | "
            f"{color}{r['rate']:>+11.6f}{RESET} | "
            f"{color}{rate_to_bps(r['rate']):>+10.2f}{RESET} | "
            f"{color}{rate_to_annualized_pct(r['rate'], r['exchange']):>+9.2f}{RESET} | "
            f"{format_utc_ms(r['next_time_ms']):<20}"
        )
    print("-" * 80)


def display_detail(d: Dict) -> None:
    if not d:
        print(f"{RED}No detail available.{RESET}")
        return
    rate = d["rate"]
    color = _rate_color(rate)
    print()
    print(f"{BOLD}{CYAN}{d['exchange'].upper()}  ::  {d['symbol']}{RESET}")
    print("-" * 60)
    print(f"  Funding rate    : {color}{rate:+.6f}{RESET}  "
          f"({color}{rate_to_bps(rate):+.2f} bps{RESET}, "
          f"{color}{rate_to_annualized_pct(rate, d['exchange']):+.2f}% APR{RESET})")
    print(f"  Next funding    : {format_utc_ms(d['next_time_ms'])}  "
          f"({YELLOW}in {time_until(d['next_time_ms'])}{RESET})")
    print(f"  Funding interval: {d.get('interval_hours', 8)}h")
    print("-" * 60)
    print(f"  Mark price      : {_fmt(d.get('mark_price'), n=4)}")
    print(f"  Index price     : {_fmt(d.get('index_price'), n=4)}")
    print(f"  Last price      : {_fmt(d.get('last_price'), n=4)}")
    pc = d.get("price_change_pct_24h")
    pc_color = _rate_color(pc) if pc is not None else ""
    print(f"  24h change      : {pc_color}{_fmt(pc, '%', n=2)}{RESET}")
    print(f"  24h volume      : {_fmt(d.get('volume_24h'), n=0)}")
    print(f"  Open interest   : {_fmt(d.get('open_interest'), n=2)}")
    print("-" * 60)


def display_history(rows: List[Dict], exchange: str, symbol: str) -> None:
    if not rows:
        print(f"{RED}No history for {exchange.upper()} {symbol}.{RESET}")
        return
    print()
    print(f"{BOLD}{CYAN}HISTORY  {exchange.upper()} :: {symbol}  ({len(rows)} entries, newest first){RESET}")
    print("-" * 76)
    print(f"{'#':<3} | {'Timestamp (UTC)':<22} | {'Rate':>11} | {'BPS':>10} | {'APR %':>10}")
    print("-" * 76)
    for i, r in enumerate(rows, 1):
        color = _rate_color(r["rate"])
        print(
            f"{i:<3} | {format_utc_ms(r['timestamp_ms']):<22} | "
            f"{color}{r['rate']:>+11.6f}{RESET} | "
            f"{color}{rate_to_bps(r['rate']):>+10.2f}{RESET} | "
            f"{color}{rate_to_annualized_pct(r['rate'], exchange):>+10.2f}{RESET}"
        )
    print("-" * 76)
    # Summary stats
    rates = [r["rate"] for r in rows]
    avg = sum(rates) / len(rates)
    print(f"  mean={avg*10000:+.2f} bps  "
          f"min={min(rates)*10000:+.2f} bps  "
          f"max={max(rates)*10000:+.2f} bps")


def export_results(results: List[Dict], path: Path, fmt: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fmt == "json":
        path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    elif fmt == "csv":
        if not results:
            path.write_text("", encoding="utf-8")
            return
        keys = ["exchange", "coin", "rate", "next_time_ms", "fetched_at_ms"]
        with path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
            w.writeheader()
            w.writerows(results)
    else:
        raise ValueError(f"unknown export format: {fmt}")
    print(f"{GREEN}Exported {len(results)} rows to {path}{RESET}", file=sys.stderr)
