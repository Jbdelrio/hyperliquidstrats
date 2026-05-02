"""
adverse_selection_monitor.py — Detects toxic-flow adverse selection.

If our fills are consistently followed by price moving against us (i.e.,
informed traders are taking the other side), we're being picked and should
suspend the affected symbol.

pick_rate = fraction of recent trades where we incurred a loss.
Threshold: 65% (random baseline ≈ 33%, market-maker typical ≈ 40-50%).
"""
import logging
from collections import deque

log = logging.getLogger(__name__)


class AdverseSelectionMonitor:

    def __init__(self, lookback: int = 30, threshold: float = 0.65,
                 suspend_s: float = 1200.0):
        self.lookback   = lookback
        self.threshold  = threshold
        self.suspend_s  = suspend_s

        self._outcomes: dict[str, deque] = {}   # symbol → deque[bool] (True=loss)
        self._suspended: dict[str, float] = {}  # symbol → until_ts

    def record_close(self, symbol: str, was_loss: bool) -> None:
        if symbol not in self._outcomes:
            self._outcomes[symbol] = deque(maxlen=self.lookback)
        self._outcomes[symbol].append(was_loss)

    def get_pick_rate(self, symbol: str) -> float:
        buf = self._outcomes.get(symbol)
        if not buf or len(buf) < 10:
            return 0.0
        return sum(buf) / len(buf)

    def check_and_suspend(self, symbol: str, current_ts: float) -> bool:
        """Returns True if symbol should be suspended. Sets timer if newly triggered."""
        # Already suspended?
        if symbol in self._suspended and current_ts < self._suspended[symbol]:
            return True

        rate = self.get_pick_rate(symbol)
        if rate > self.threshold:
            self._suspended[symbol] = current_ts + self.suspend_s
            log.warning("[%s] Adverse selection: pick_rate=%.0f%% > %.0f%% → suspend %.0fs",
                        symbol, rate * 100, self.threshold * 100, self.suspend_s)
            return True

        return False

    def is_suspended(self, symbol: str, current_ts: float) -> bool:
        until = self._suspended.get(symbol, 0.0)
        return current_ts < until

    def get_all_pick_rates(self) -> dict[str, float]:
        return {s: self.get_pick_rate(s) for s in self._outcomes}
