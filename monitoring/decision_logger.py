"""
decision_logger.py — Decision-level CSV logger for S8 EMS debug analysis.

Logs every PLACE / SKIP decision with full sensor context (spread, Hurst,
HAR-RV forecast, Kalman FV, OBI).  Buffer auto-flushed at 500 rows and
manually on engine shutdown via flush().
"""
import csv
import time
import threading
from pathlib import Path
from typing import Optional


_FIELDNAMES = [
    "timestamp", "symbol", "decision", "reason",
    "mid", "spread_bps", "hurst", "har_rv_forecast",
    "kalman_fv", "obi",
    "buy_price", "sell_price", "size", "notional_usd",
]

_AUTO_FLUSH_ROWS = 500


class DecisionLogger:
    """Thread-safe buffered CSV logger for strategy decisions."""

    def __init__(self, path: str = "logs/decisions_v9.csv", enabled: bool = True):
        self.enabled = enabled
        self._path = Path(path)
        self._rows: list = []
        self._lock = threading.Lock()

        if enabled:
            self._path.parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def log_skip(self, symbol: str, reason: str,
                 timestamp: Optional[float] = None,
                 mid: Optional[float] = None,
                 spread_bps: Optional[float] = None,
                 hurst: Optional[float] = None,
                 har_rv_forecast: Optional[float] = None,
                 kalman_fv: Optional[float] = None,
                 obi: Optional[float] = None) -> None:
        if not self.enabled:
            return
        self._append({
            "timestamp":      timestamp if timestamp is not None else time.time(),
            "symbol":         symbol,
            "decision":       "SKIP",
            "reason":         reason,
            "mid":            mid,
            "spread_bps":     spread_bps,
            "hurst":          hurst,
            "har_rv_forecast": har_rv_forecast,
            "kalman_fv":      kalman_fv,
            "obi":            obi,
            "buy_price":      None,
            "sell_price":     None,
            "size":           None,
            "notional_usd":   None,
        })

    def log_place(self, symbol: str,
                  timestamp: Optional[float] = None,
                  mid: Optional[float] = None,
                  spread_bps: Optional[float] = None,
                  hurst: Optional[float] = None,
                  har_rv_forecast: Optional[float] = None,
                  kalman_fv: Optional[float] = None,
                  obi: Optional[float] = None,
                  buy_price: Optional[float] = None,
                  sell_price: Optional[float] = None,
                  size: Optional[float] = None,
                  notional_usd: Optional[float] = None) -> None:
        if not self.enabled:
            return
        self._append({
            "timestamp":      timestamp if timestamp is not None else time.time(),
            "symbol":         symbol,
            "decision":       "PLACE",
            "reason":         "",
            "mid":            mid,
            "spread_bps":     spread_bps,
            "hurst":          hurst,
            "har_rv_forecast": har_rv_forecast,
            "kalman_fv":      kalman_fv,
            "obi":            obi,
            "buy_price":      buy_price,
            "sell_price":     sell_price,
            "size":           size,
            "notional_usd":   notional_usd,
        })

    def flush(self) -> None:
        """Write buffered rows to CSV and clear buffer.  Safe to call any time."""
        if not self.enabled:
            return
        with self._lock:
            rows = list(self._rows)
            self._rows.clear()
        if not rows:
            return
        write_header = not self._path.exists()
        with open(self._path, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            if write_header:
                writer.writeheader()
            writer.writerows(rows)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _append(self, row: dict) -> None:
        with self._lock:
            self._rows.append(row)
            should_flush = len(self._rows) >= _AUTO_FLUSH_ROWS
        if should_flush:
            self.flush()
