"""
backtesting/data_loader.py — Read fills_v9.csv into trade dicts.

Also provides a stub load_ohlcv(symbol, interval, start, end) that raises
NotImplementedError — to be wired to a real data source later.
"""
from __future__ import annotations

import csv
import time
from datetime import datetime
from pathlib import Path


_DEFAULT_FILLS_PATH = "logs/fills_v9.csv"


def _parse_ts(raw: str) -> float:
    """Accept either ISO 8601 'YYYY-mm-ddTHH:MM:SS' or numeric epoch."""
    if not raw:
        return 0.0
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(raw).timestamp()
    except Exception:
        return 0.0


def _float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_fills_as_trades(path: str = _DEFAULT_FILLS_PATH) -> list[dict]:
    """
    Read the engine's fills_v9.csv and return a list of trade dicts in the
    format expected by metrics.compute_metrics().

    Columns expected (older logs may not have slippage_bps):
      ts, symbol, side, notional, entry, exit, gross, fee, net,
      hold_s, reason, strategy, [slippage_bps]
    """
    p = Path(path)
    if not p.exists():
        return []

    trades: list[dict] = []
    with open(p, encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append({
                "ts":       _parse_ts(row.get("ts", "")),
                "symbol":   row.get("symbol", ""),
                "strategy": row.get("strategy", ""),
                "side":     row.get("side", ""),
                "notional": _float(row.get("notional", 0)),
                "entry":    _float(row.get("entry", 0)),
                "exit":     _float(row.get("exit", 0)),
                "gross":    _float(row.get("gross", 0)),
                "fee":      _float(row.get("fee", 0)),
                "net":      _float(row.get("net", 0)),
                "hold_s":   _float(row.get("hold_s", 0)),
                "reason":   row.get("reason", ""),
                "slippage_bps": _float(row.get("slippage_bps", 0)),
            })
    return trades


def load_ohlcv(symbol: str, interval: str,
               start: float, end: float) -> list[dict]:
    """
    Stub OHLCV loader. Wire this to your historical data source
    (Hyperliquid REST, CSV cache, Binance dump, …).

    Until wired, this raises NotImplementedError with guidance.
    """
    raise NotImplementedError(
        f"load_ohlcv({symbol!r}, {interval!r}, {start}, {end}) is a stub. "
        "Implement a data fetch (REST / parquet cache / CSV) before running "
        "the BacktestEngine, or feed pre-computed bars directly."
    )
