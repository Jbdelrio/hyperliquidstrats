"""
bar_aggregator.py — Accumulates 1-min BarData objects into N-min bars.

Usage:
    agg = BarAggregator(symbol="BTC", tf_minutes=15, maxlen=300)
    completed = agg.update(bar_1m)   # returns BarData or None
    closes = agg.closes()            # list of completed-bar closes
"""
from collections import deque
from typing import Optional

from strategies.base_strategy import BarData


class BarAggregator:
    """Rolls up 1-minute bars into a fixed-N-minute candle."""

    __slots__ = ("symbol", "tf", "_maxlen", "_pending", "_completed", "_bars")

    def __init__(self, symbol: str, tf_minutes: int, maxlen: int = 500):
        self.symbol   = symbol
        self.tf       = tf_minutes
        self._maxlen  = maxlen
        self._pending: list[BarData] = []
        self._bars:    deque[BarData] = deque(maxlen=maxlen)

    # ------------------------------------------------------------------

    def update(self, bar_1m: BarData) -> Optional[BarData]:
        """
        Feed one 1-minute bar. Returns a completed N-min BarData when the
        window is full, otherwise None.
        """
        self._pending.append(bar_1m)
        if len(self._pending) < self.tf:
            return None

        bars     = self._pending
        completed = BarData(
            symbol    = self.symbol,
            ts        = bars[-1].ts,
            open      = bars[0].open,
            high      = max(b.high for b in bars),
            low       = min(b.low  for b in bars),
            close     = bars[-1].close,
            volume_usd = sum(b.volume_usd for b in bars),
            return_1m = ((bars[-1].close - bars[0].open) / bars[0].open
                         if bars[0].open else 0.0),
        )
        self._bars.append(completed)
        self._pending = []
        return completed

    # ------------------------------------------------------------------
    # Convenience accessors

    def closes(self) -> list[float]:
        return [b.close for b in self._bars]

    def opens(self) -> list[float]:
        return [b.open for b in self._bars]

    def highs(self) -> list[float]:
        return [b.high for b in self._bars]

    def lows(self) -> list[float]:
        return [b.low for b in self._bars]

    def volumes(self) -> list[float]:
        return [b.volume_usd for b in self._bars]

    def last(self) -> Optional[BarData]:
        return self._bars[-1] if self._bars else None

    def __len__(self) -> int:
        return len(self._bars)
