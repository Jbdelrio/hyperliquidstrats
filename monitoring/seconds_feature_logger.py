"""
seconds_feature_logger.py — CSV logger for per-second microstructure features.

Writes at most ONE row per (symbol, second) to `logs/seconds_features.csv`.
Header is written automatically the first time the file is created.
Rows are buffered and flushed every `flush_rows` writes.

The logger is intentionally dumb : it does no feature computation, only
serialization. Compute features via `SecondsFeatureEngine` then pass the
snapshot dict to `log(features)`.
"""
from __future__ import annotations

import csv
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


FIELDNAMES = [
    "ts",
    "datetime",
    "symbol",
    "mid",
    "best_bid",
    "best_ask",
    "spread_bps",
    "obi_1",
    "obi_3",
    "obi_5",
    "obi_10",
    "trade_imbalance_5s",
    "trade_imbalance_10s",
    "trade_imbalance_30s",
    "buy_volume_usd_10s",
    "sell_volume_usd_10s",
    "vwap_5s",
    "vwap_15s",
    "vwap_30s",
    "vwap_slope_5_30",
    "microprice",
    "microprice_pressure",
    "r_5s",
    "r_15s",
    "r_30s",
    "rv_30s",
    "rv_60s",
    "book_flow_alignment",
    "book_flow_divergence",
    "absorption_sell_proxy",
    "absorption_buy_proxy",
    "liquidity_vacuum",
    "pressure_score_raw",
    "book_stale",
    "enough_data",
]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return f"{v:.10g}"
    if isinstance(v, bool):
        return "1" if v else "0"
    return v


class SecondsFeatureLogger:

    def __init__(self,
                 path: str = "logs/seconds_features.csv",
                 min_interval_s: float = 1.0,
                 flush_rows: int = 200):
        self.path = Path(path)
        self.min_interval_s = float(min_interval_s)
        self.flush_rows = int(flush_rows)
        self._last_log_ts: dict[str, float] = {}
        self._buf: list[list] = []
        self._lock = threading.Lock()

        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            with open(self.path, "w", newline="", encoding="utf-8") as fh:
                csv.writer(fh).writerow(FIELDNAMES)

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    def log(self, features: dict) -> bool:
        """Buffer a feature snapshot. Returns True if accepted, False if rate-limited."""
        sym = features.get("symbol")
        if not sym:
            return False
        ts = float(features.get("ts", time.time()))
        last = self._last_log_ts.get(sym, 0.0)
        if ts - last < self.min_interval_s:
            return False

        # Build row in FIELDNAMES order
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()
        row = []
        for f in FIELDNAMES:
            if f == "ts":
                row.append(f"{ts:.3f}")
            elif f == "datetime":
                row.append(dt)
            elif f == "symbol":
                row.append(sym)
            else:
                row.append(_fmt(features.get(f)))

        with self._lock:
            self._buf.append(row)
            self._last_log_ts[sym] = ts
            if len(self._buf) >= self.flush_rows:
                self._flush_locked()
        return True

    def flush(self) -> None:
        with self._lock:
            self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buf:
            return
        rows = self._buf
        self._buf = []
        with open(self.path, "a", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerows(rows)
