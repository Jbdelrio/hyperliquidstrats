"""
trades_buffer.py — Per-symbol circular buffer for recent market trades.
"""
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional


@dataclass(slots=True)
class Trade:
    timestamp: float
    price: float
    size: float
    side: str          # "B" = taker buy, "A" = taker sell (Hyperliquid convention)
    volume_usd: float


class TradesBuffer:
    def __init__(self, maxlen: int = 2000):
        self._buf: deque[Trade] = deque(maxlen=maxlen)

    def add(self, trade: Trade) -> None:
        self._buf.append(trade)

    def get_recent(self, seconds: float) -> list[Trade]:
        cutoff = time.time() - seconds
        return [t for t in self._buf if t.timestamp >= cutoff]

    def get_vwap(self, seconds: float = 30.0) -> Optional[float]:
        recent = self.get_recent(seconds)
        if not recent:
            return None
        total_vol = sum(t.volume_usd for t in recent)
        if total_vol <= 0:
            return None
        return sum(t.price * t.volume_usd for t in recent) / total_vol

    def __len__(self) -> int:
        return len(self._buf)
