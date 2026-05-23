"""Shared helpers — formatting, color, arbitrage maths."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Dict, Iterable, List


def format_utc_ms(ts_ms: int) -> str:
    if not ts_ms:
        return "—"
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def rate_to_bps(rate: float) -> float:
    return rate * 10_000


# Fundings per day per exchange — most fund every 8h (3/day), some hourly (24/day).
FUNDINGS_PER_DAY = {
    "binance": 3,
    "aster": 3,
    "bitget": 3,
    "okx": 3,
    "gateio": 3,
    "phemex": 3,
    "bitmex": 3,
    "hyperliquid": 24,
    "kraken": 24,
}


def time_until(target_ms: int) -> str:
    """Return human-readable countdown like '4h 23m' or '12m 5s' until target."""
    import time as _t
    diff_s = int(target_ms / 1000 - _t.time())
    if diff_s <= 0:
        return "now"
    h, rem = divmod(diff_s, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def rate_to_annualized_pct(rate: float, exchange: str | None = None,
                            fundings_per_day: int | None = None) -> float:
    """Annualised funding %; defaults to 8h cadence unless exchange is hourly."""
    if fundings_per_day is None:
        fundings_per_day = FUNDINGS_PER_DAY.get(exchange or "", 3)
    return rate * fundings_per_day * 365 * 100


def calculate_arbitrage(results: Iterable[Dict]) -> List[Dict]:
    """For each coin, return the (low-rate, high-rate) exchange pair sorted by spread."""
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        grouped[r["coin"]].append(r)

    opps: List[Dict] = []
    for coin, rows in grouped.items():
        if len(rows) < 2:
            continue
        rows_sorted = sorted(rows, key=lambda x: x["rate"])
        low, high = rows_sorted[0], rows_sorted[-1]
        spread = high["rate"] - low["rate"]
        opps.append({
            "coin": coin,
            "long_exchange": low["exchange"],
            "short_exchange": high["exchange"],
            "long_rate": low["rate"],
            "short_rate": high["rate"],
            "spread": spread,
            "spread_bps": rate_to_bps(spread),
            "next_time_ms": min(low["next_time_ms"], high["next_time_ms"]),
        })
    opps.sort(key=lambda x: x["spread_bps"], reverse=True)
    return opps


def calculate_comparison(results: Iterable[Dict]) -> Dict[str, Dict]:
    """Return {coin: {rows, avg, min, max, spread}} for compare-mode tables."""
    grouped: Dict[str, List[Dict]] = defaultdict(list)
    for r in results:
        grouped[r["coin"]].append(r)

    out: Dict[str, Dict] = {}
    for coin, rows in grouped.items():
        rates = [r["rate"] for r in rows]
        avg = sum(rates) / len(rates) if rates else 0.0
        out[coin] = {
            "rows": sorted(rows, key=lambda x: x["rate"]),
            "avg": avg,
            "min": min(rates) if rates else 0.0,
            "max": max(rates) if rates else 0.0,
            "spread": (max(rates) - min(rates)) if rates else 0.0,
        }
    return out
