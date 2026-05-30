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

# Absolute repo root — works regardless of CWD
_REPO = Path(__file__).parent.parent

DECISIONS_PATH = str(_REPO / "logs/decisions_v9.csv")
FILLS_PATH     = str(_REPO / "logs/fills_v9.csv")
METRICS_PATH   = str(_REPO / "metrics_v9/metrics_v9.csv")

_cache: dict = {}


def _cached_csv(path: str) -> pd.DataFrame:
    """Read a CSV with mtime-based cache.

    Returns a defensive **copy** on every call — Dash fires multiple callback
    threads in parallel and they all mutate `df['col'] = ...`. Sharing the
    cached object caused `IndexError: index 1 is out of bounds for axis 0
    with size 1` deep in pandas' block manager (race between concurrent
    column assignments). The cost of one copy per call is negligible vs.
    parsing the file again.
    """
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        mtime = p.stat().st_mtime
        if path in _cache and _cache[path][0] == mtime:
            return _cache[path][1].copy()
        df = pd.read_csv(p, low_memory=False, on_bad_lines="skip")
        # Drop duplicate columns that appear when CSV is read mid-write
        # .copy() is required: loc slicing leaves internal block indices inconsistent
        df = df.loc[:, ~df.columns.duplicated()].copy().reset_index(drop=True)
        _cache[path] = (mtime, df)
        return df.copy()
    except Exception:
        return pd.DataFrame()


def load_decisions() -> pd.DataFrame:
    df = _cached_csv(DECISIONS_PATH)
    if df.empty:
        return df
    if "decision" not in df.columns:
        # File corrupted (e.g. git-lfs pointer or merge conflict markers)
        return pd.DataFrame()
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
    # Guard every conversion — a half-flushed CSV row can corrupt one column
    # without invalidating the whole frame; we want the rest to still render.
    try:
        if "ts" in df.columns:
            df["dt"] = pd.to_datetime(df["ts"], errors="coerce")
    except Exception:
        df["dt"] = pd.NaT
    for col in ("notional", "entry", "exit", "gross", "fee", "net", "hold_s"):
        if col not in df.columns:
            continue
        try:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        except Exception:
            df[col] = pd.NA
    # Backfill strategy column if missing (old CSV format without strategy column)
    if "strategy" not in df.columns:
        df["strategy"] = ""
    return df


def load_metrics() -> pd.DataFrame:
    df = _cached_csv(METRICS_PATH)
    if df.empty:
        return df
    try:
        if "ts" in df.columns:
            df["dt"] = pd.to_datetime(df["ts"], errors="coerce")
        for col in ("equity", "pnl_min", "pnl_hour", "pnl_day",
                    "win_rate", "avg_hold_s", "pick_rate",
                    "wins", "losses", "stops", "tps", "max_holds"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
    except Exception:
        pass
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

STRATEGY_STATUS_PATH = str(_REPO / "runtime/strategy_status.json")
CALIBRATION_PATH     = str(_REPO / "runtime/calibration_data.json")

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
