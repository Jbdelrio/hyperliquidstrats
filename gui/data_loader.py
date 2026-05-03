"""
data_loader.py — Read-only CSV loaders with mtime-based cache.

All paths are relative to the repo root (where `python -m gui.app` is run).
Returns empty DataFrames when files are missing — never raises on missing data.
"""
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd

DECISIONS_PATH = "logs/decisions_v9.csv"
FILLS_PATH     = "logs/fills_v9.csv"
METRICS_PATH   = "metrics_v9/metrics_v9.csv"

_cache: dict = {}


def _cached_csv(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        mtime = p.stat().st_mtime
        if path in _cache and _cache[path][0] == mtime:
            return _cache[path][1]
        df = pd.read_csv(p, low_memory=False)
        _cache[path] = (mtime, df)
        return df
    except Exception:
        return pd.DataFrame()


def load_decisions() -> pd.DataFrame:
    df = _cached_csv(DECISIONS_PATH)
    if df.empty:
        return df
    if "timestamp" in df.columns:
        df["dt"] = pd.to_datetime(df["timestamp"], unit="s", utc=True, errors="coerce")
    for col in ("spread_bps", "hurst", "har_rv_forecast", "kalman_fv", "obi",
                "mid", "buy_price", "sell_price", "size", "notional_usd"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_fills() -> pd.DataFrame:
    df = _cached_csv(FILLS_PATH)
    if df.empty:
        return df
    if "ts" in df.columns:
        df["dt"] = pd.to_datetime(df["ts"], errors="coerce")
    for col in ("notional", "entry", "exit", "gross", "fee", "net", "hold_s"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_metrics() -> pd.DataFrame:
    df = _cached_csv(METRICS_PATH)
    if df.empty:
        return df
    if "ts" in df.columns:
        df["dt"] = pd.to_datetime(df["ts"], errors="coerce")
    for col in ("equity", "pnl_min", "pnl_hour", "pnl_day",
                "win_rate", "avg_hold_s", "pick_rate",
                "wins", "losses", "stops", "tps", "max_holds"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def recent_decisions(hours: float = 2.0) -> pd.DataFrame:
    import time
    df = load_decisions()
    if df.empty or "timestamp" not in df.columns:
        return df
    cutoff = time.time() - hours * 3600
    return df[df["timestamp"] >= cutoff].copy()


# ---------------------------------------------------------------------------
# Runtime JSON loaders (written by engine every 60s)
# ---------------------------------------------------------------------------

STRATEGY_STATUS_PATH = "runtime/strategy_status.json"
CALIBRATION_PATH     = "runtime/calibration_data.json"

_json_cache: dict = {}


def _cached_json(path: str):
    p = Path(path)
    if not p.exists():
        return None
    try:
        mtime = p.stat().st_mtime
        if path in _json_cache and _json_cache[path][0] == mtime:
            return _json_cache[path][1]
        with open(p) as f:
            data = json.load(f)
        _json_cache[path] = (mtime, data)
        return data
    except Exception:
        return None


def load_strategy_status() -> list:
    data = _cached_json(STRATEGY_STATUS_PATH)
    return data if isinstance(data, list) else []


def load_calibration() -> dict:
    data = _cached_json(CALIBRATION_PATH)
    return data if isinstance(data, dict) else {}
