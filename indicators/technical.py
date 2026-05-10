"""
technical.py — Pure-Python technical indicators for Artemisia strategies.

All functions are stateless: they accept a Python list/sequence and return a scalar
(or tuple), or None when there is not enough data. No pandas/numpy dependency.

EMA note: for proper EMA warmup, strategies should maintain a running EmaState
(see below) rather than recomputing from scratch on every call.
"""
import math
from typing import Optional, Sequence


# ---------------------------------------------------------------------------
# Moving averages
# ---------------------------------------------------------------------------

def sma(values: Sequence[float], period: int) -> Optional[float]:
    """Simple moving average of the last `period` values."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def ema(values: Sequence[float], period: int) -> Optional[float]:
    """
    Exponential moving average — seeds on the first SMA(period) value then
    applies the standard EMA formula. Adequate for offline/one-shot calls;
    for hot-path use EmaState instead.
    """
    n = len(values)
    if n < period:
        return None
    k = 2.0 / (period + 1)
    result = sum(values[:period]) / period
    for v in values[period:]:
        result = v * k + result * (1.0 - k)
    return result


# ---------------------------------------------------------------------------
# EmaState — incremental EMA for real-time use in strategy hot paths
# ---------------------------------------------------------------------------

class EmaState:
    """
    Maintains a running EMA without reprocessing the full history each tick.

    Usage:
        state = EmaState(period=50)
        for bar in bars:
            current_ema = state.update(bar.close)
    """
    __slots__ = ("period", "_k", "_value", "_count")

    def __init__(self, period: int):
        self.period  = period
        self._k      = 2.0 / (period + 1)
        self._value: Optional[float] = None
        self._count  = 0

    def update(self, price: float) -> float:
        if self._value is None:
            self._value = price
        else:
            self._value = price * self._k + self._value * (1.0 - self._k)
        self._count += 1
        return self._value

    @property
    def value(self) -> Optional[float]:
        return self._value

    def ready(self, min_bars: Optional[int] = None) -> bool:
        """Returns True once enough bars have been seen for a reliable estimate."""
        needed = min_bars or self.period // 2
        return self._count >= needed

    def reset(self) -> None:
        self._value = None
        self._count = 0


# ---------------------------------------------------------------------------
# Oscillators
# ---------------------------------------------------------------------------

def rsi(closes: Sequence[float], period: int = 14) -> Optional[float]:
    """
    RSI using Wilder's smoothing (same as TradingView default).
    Requires at least period+1 values.
    """
    n = len(closes)
    if n < period + 1:
        return None

    gains, losses = [], []
    for i in range(1, n):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0.0))
        losses.append(max(-diff, 0.0))

    # Initial averages over the first `period` moves
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder smoothing for remaining data
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


# ---------------------------------------------------------------------------
# Volatility / bands
# ---------------------------------------------------------------------------

def bollinger(closes: Sequence[float], period: int = 20,
              k: float = 2.0) -> Optional[tuple[float, float, float]]:
    """Bollinger Bands. Returns (upper, mid, lower) or None."""
    if len(closes) < period:
        return None
    data = list(closes[-period:])
    mid  = sum(data) / period
    var  = sum((x - mid) ** 2 for x in data) / period
    std  = math.sqrt(var)
    return mid + k * std, mid, mid - k * std


def donchian(highs: Sequence[float], lows: Sequence[float],
             period: int) -> Optional[tuple[float, float, float]]:
    """Donchian Channel over `period` bars. Returns (upper, mid, lower)."""
    if len(highs) < period or len(lows) < period:
        return None
    upper = max(highs[-period:])
    lower = min(lows[-period:])
    return upper, (upper + lower) / 2.0, lower


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> Optional[float]:
    """Average True Range (simple mean of last `period` TRs)."""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs = []
    for i in range(1, n):
        tr = max(
            highs[i]  - lows[i],
            abs(highs[i]  - closes[i - 1]),
            abs(lows[i]   - closes[i - 1]),
        )
        trs.append(tr)
    return sum(trs[-period:]) / period


# ---------------------------------------------------------------------------
# Statistical
# ---------------------------------------------------------------------------

def zscore(values: Sequence[float], period: int) -> Optional[float]:
    """Z-score of the last value relative to its `period`-bar distribution."""
    if len(values) < period:
        return None
    data  = list(values[-period:])
    mu    = sum(data) / period
    var   = sum((x - mu) ** 2 for x in data) / period
    if var == 0.0:
        return 0.0
    return (values[-1] - mu) / math.sqrt(var)


def rolling_beta(y_values: Sequence[float],
                 x_values: Sequence[float],
                 period: int) -> Optional[tuple[float, float]]:
    """
    OLS slope (beta) and intercept (alpha) of y ~ alpha + beta*x
    using the last `period` values. Returns (beta, alpha) or None.
    """
    if len(y_values) < period or len(x_values) < period:
        return None
    y = list(y_values[-period:])
    x = list(x_values[-period:])
    n = period
    sx  = sum(x)
    sy  = sum(y)
    sxy = sum(x[i] * y[i] for i in range(n))
    sxx = sum(xi ** 2 for xi in x)
    denom = n * sxx - sx * sx
    if denom == 0.0:
        return None
    beta  = (n * sxy - sx * sy) / denom
    alpha = (sy - beta * sx) / n
    return beta, alpha


# ---------------------------------------------------------------------------
# Volume
# ---------------------------------------------------------------------------

def volume_sma(volumes: Sequence[float], period: int) -> Optional[float]:
    return sma(volumes, period)
