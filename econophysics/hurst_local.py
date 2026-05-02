"""
hurst_local.py — Local Hurst exponent estimator (2-timescale method).
Ref: Cours Garcin (2020), diapos 44-46.

H < 0.5 : mean-reverting  → good for market making, upsize
H = 0.5 : random walk     → neutral
H > 0.5 : trending        → reduce/stop quoting
"""
import numpy as np
from collections import deque
from typing import Optional


class HurstLocalEstimator:
    """
    Fast Hurst estimator via lag-1 vs lag-2 variance ratio.

    For a fractional Brownian motion:
        Var[X(t+h) - X(t)] ∝ h^(2H)
    So: Var_lag2 / Var_lag1 = 2^(2H)  →  H = 0.5 * log2(var2 / var1)

    Performance: ~50µs per update at N=300 with cache_ticks=5.
    """

    def __init__(self, window: int = 300, min_samples: int = 100,
                 cache_ticks: int = 5):
        self.window = window
        self.min_samples = min_samples
        self.cache_ticks = cache_ticks

        self.log_prices: deque = deque(maxlen=window)
        self._cached_h: Optional[float] = None
        self._ticks_since_update: int = 0

    def update(self, price: float) -> Optional[float]:
        """Add a new price. Returns current H estimate (or None if warming up)."""
        if price <= 0:
            return self._cached_h

        self.log_prices.append(np.log(price))
        self._ticks_since_update += 1

        if len(self.log_prices) < self.min_samples:
            return None

        if self._cached_h is not None and self._ticks_since_update < self.cache_ticks:
            return self._cached_h

        self._ticks_since_update = 0
        self._cached_h = self._compute()
        return self._cached_h

    def _compute(self) -> float:
        prices = np.array(self.log_prices)

        # Lag-1 increments: x[i+1] - x[i]
        inc1 = prices[1:] - prices[:-1]
        # Lag-2 increments: x[i+2] - x[i]  (NOT second differences)
        inc2 = prices[2:] - prices[:-2]

        var1 = np.var(inc1)
        var2 = np.var(inc2)

        if var1 <= 1e-14 or var2 <= 1e-14:
            return 0.5

        # For fBm: var2 / var1 = 2^(2H)
        H = 0.5 * np.log2(var2 / var1)
        return float(np.clip(H, 0.05, 0.95))

    def get_regime(self) -> str:
        if self._cached_h is None:
            return "UNKNOWN"
        h = self._cached_h
        if h < 0.45:
            return "MR"
        elif h <= 0.55:
            return "RW"
        elif h < 0.65:
            return "TREND_LOW"
        else:
            return "TREND_HIGH"

    def get_size_multiplier(self) -> float:
        return {
            "MR":         1.4,
            "RW":         1.0,
            "TREND_LOW":  0.6,
            "TREND_HIGH": 0.0,
            "UNKNOWN":    0.5,
        }[self.get_regime()]

    @property
    def h(self) -> Optional[float]:
        return self._cached_h
