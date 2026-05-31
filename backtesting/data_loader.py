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


# ── PHASE 0 wiring : chargement des parquets historiques HL ──────────────────
# Produits par data/historical_data.py → data/historical/{coin}_{interval}.parquet

_HIST_DIR = Path(__file__).resolve().parents[1] / "data" / "historical"


def historical_path(coin: str, interval: str) -> Path:
    return _HIST_DIR / f"{coin}_{interval}.parquet"


def load_historical_bars(coin: str, interval: str,
                         start: float = 0.0, end: float = 0.0) -> list:
    """
    Charge data/historical/{coin}_{interval}.parquet en objets BarData
    (ts en SECONDES, volume_usd = volume×close, return_1m = variation close).
    `start`/`end` en secondes epoch filtrent la fenêtre (0 = pas de borne).
    Zéro look-ahead : on renvoie les barres telles quelles, triées par ts.
    """
    from strategies.base_strategy import BarData  # import local (évite cycle)
    import pandas as pd  # local

    p = historical_path(coin, interval)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} introuvable. Lance d'abord : python -m data.historical_data")
    df = pd.read_parquet(p).sort_values("ts").reset_index(drop=True)
    ts_s = df["ts"].astype("int64") / 1000.0
    if start:
        df = df[ts_s >= start]
    if end:
        df = df[ts_s <= end]
    df = df.reset_index(drop=True)
    closes = df["close"].to_numpy(dtype=float)
    bars: list = []
    for i, row in df.iterrows():
        r = 0.0 if i == 0 or closes[i - 1] <= 0 else (closes[i] - closes[i - 1]) / closes[i - 1]
        bars.append(BarData(
            symbol=coin, ts=float(row["ts"]) / 1000.0,
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume_usd=float(row["volume"]) * float(row["close"]),
            return_1m=float(r),
        ))
    return bars


def load_funding_series(coin: str) -> list:
    """
    Charge le funding historique → liste de (time_s, funding_rate) triée.
    Utilisée par BacktestEngine pour l'accrual aux frontières de funding.
    Renvoie [] si le parquet funding n'existe pas.
    """
    import pandas as pd  # local
    p = _HIST_DIR / f"{coin}_funding.parquet"
    if not p.exists():
        return []
    df = pd.read_parquet(p).sort_values("time").reset_index(drop=True)
    return [(float(t) / 1000.0, float(fr))
            for t, fr in zip(df["time"], df["funding_rate"])]


def load_ohlcv(symbol: str, interval: str,
               start: float = 0.0, end: float = 0.0) -> list[dict]:
    """OHLCV en dicts (ts en secondes) depuis le cache historique HL."""
    bars = load_historical_bars(symbol, interval, start, end)
    return [{"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
             "close": b.close, "volume_usd": b.volume_usd} for b in bars]
