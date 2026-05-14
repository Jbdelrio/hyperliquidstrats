"""
trades_buffer.py — Per-symbol circular buffer for recent market trades.

Side convention (Hyperliquid) : "B" = taker buy, "A" = taker sell.

The buffer is both size-bounded (maxlen) AND time-bounded
(max_age_seconds, pruned on demand). Time-based access methods
(get_buy_sell_volume, get_trade_imbalance, get_trade_count) read directly
from the deque without mutating it.
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
    def __init__(self, maxlen: int = 2000, max_age_seconds: float = 300.0):
        self._buf: deque[Trade] = deque(maxlen=maxlen)
        self.max_age_seconds = float(max_age_seconds)

    # ------------------------------------------------------------------
    # Mutators
    # ------------------------------------------------------------------

    def add(self, trade: Trade) -> None:
        self._buf.append(trade)

    def prune_old(self, now: Optional[float] = None) -> int:
        """Drop trades older than `max_age_seconds`. Returns number dropped.

        Optional safety net — the buffer is already bounded by `maxlen`,
        but pruning by time keeps memory tight when trading is sparse.
        """
        now = now if now is not None else time.time()
        cutoff = now - self.max_age_seconds
        dropped = 0
        while self._buf and self._buf[0].timestamp < cutoff:
            self._buf.popleft()
            dropped += 1
        return dropped

    # ------------------------------------------------------------------
    # Read-only access
    # ------------------------------------------------------------------

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

    def get_buy_sell_volume(self, seconds: float) -> tuple[float, float]:
        """Return (buy_volume_usd, sell_volume_usd) over the last `seconds`.

        Buy = side "B" (taker buy), Sell = side "A" (taker sell).
        """
        cutoff = time.time() - seconds
        buy = 0.0
        sell = 0.0
        for t in self._buf:
            if t.timestamp < cutoff:
                continue
            if t.side == "B":
                buy += t.volume_usd
            elif t.side == "A":
                sell += t.volume_usd
        return buy, sell

    def get_trade_imbalance(self, seconds: float) -> Optional[float]:
        """(V_buy - V_sell) / (V_buy + V_sell), or None if no volume."""
        buy, sell = self.get_buy_sell_volume(seconds)
        denom = buy + sell
        if denom <= 0:
            return None
        return (buy - sell) / denom

    def get_trade_count(self, seconds: float) -> int:
        cutoff = time.time() - seconds
        return sum(1 for t in self._buf if t.timestamp >= cutoff)

    def __len__(self) -> int:
        return len(self._buf)
