"""
bouchaud_impact.py — Order-flow pressure via Bouchaud impact model.
Ref: Cours Garcin (2020), modèle Bouchaud.

p_t - p_0 = Σ_{s<t} G(t-s) × ε_s × S_s × V_s^r

Uses exponential decay kernel G(t) = exp(-t/τ).
Returns pressure ∈ [-1, +1] to skew market-making quotes.
"""
import numpy as np
from collections import deque
from typing import Optional


class BouchaudImpactModel:
    """
    Tracks buy/sell imbalance in recent order flow.

    pressure > 0 : buy-side dominant → skew quotes up (we prefer to sell)
    pressure < 0 : sell-side dominant → skew quotes down (we prefer to buy)
    """

    def __init__(self,
                 r: float = 0.5,
                 decay_s: float = 30.0,
                 max_history: int = 2000):
        self.r = r
        self.decay = decay_s
        # (timestamp, sign, spread, volume_usd)
        self._trades: deque = deque(maxlen=max_history)

    def add_trade(self, timestamp: float, price: float, volume_usd: float,
                  best_bid: float, best_ask: float,
                  side: Optional[str] = None) -> None:
        """
        Register a market trade.

        side: "B" = taker buy (Hyperliquid convention), "A" = taker sell.
        If side is None, infer from price vs bid/ask.
        """
        if side == "B":
            sign = +1
        elif side == "A":
            sign = -1
        else:
            # Heuristic fallback
            if price >= best_ask:
                sign = +1
            elif price <= best_bid:
                sign = -1
            else:
                return  # ambiguous, skip

        spread = max(best_ask - best_bid, 1e-10)
        self._trades.append((timestamp, sign, spread, volume_usd))

    def get_pressure(self, current_ts: float) -> float:
        """
        Compute normalised pressure in [-1, +1].
        Discards trades older than 5×τ.
        """
        if not self._trades:
            return 0.0

        cutoff = current_ts - 5 * self.decay
        total_impact = 0.0
        total_weight = 0.0

        for ts, sign, spread, vol in self._trades:
            if ts < cutoff:
                continue
            g = np.exp(-(current_ts - ts) / self.decay)
            w = g * spread * (max(vol, 1e-10) ** self.r)
            total_impact += w * sign
            total_weight += w

        if total_weight <= 1e-12:
            return 0.0
        return float(np.clip(total_impact / total_weight, -1.0, 1.0))

    def get_quote_skew(self, current_ts: float, spread: float) -> float:
        """
        Dollar skew to apply to both quote legs.
        Positive → move quotes up (anticipate price rise).
        """
        pressure = self.get_pressure(current_ts)
        return pressure * spread * 0.30
