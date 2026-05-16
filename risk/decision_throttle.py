"""
decision_throttle.py — Global rate limit on trade ENTRIES.

The goal of the framework is to use seconds features as a filter, NOT to
trade every second. The throttle complements the strategy-level cooldown
with global guarantees :

  - min seconds between entries on the SAME symbol,
  - min seconds between entries from the SAME strategy,
  - max entries on a symbol per hour,
  - max entries globally per hour.

Only counts ENTRIES (PLACE_BUY / PLACE_SELL / PLACE_QUOTES). Exits and
SKIPs pass through unfiltered.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional


_ENTRY_ACTIONS = {"PLACE_BUY", "PLACE_SELL", "PLACE_QUOTES"}


@dataclass
class ThrottleStats:
    total_evaluated: int = 0
    total_blocked: int = 0
    blocks_by_reason: dict = field(default_factory=dict)


class DecisionThrottle:

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.min_per_symbol_s = float(cfg.get("min_seconds_between_entries_per_symbol", 60))
        self.min_per_strategy_s = float(cfg.get("min_seconds_between_entries_per_strategy", 120))
        self.max_per_symbol_h = int(cfg.get("max_entries_per_symbol_per_hour", 3))
        self.max_global_h = int(cfg.get("max_entries_global_per_hour", 6))

        self._last_symbol_ts: dict[str, float] = {}
        self._last_strategy_ts: dict[str, float] = {}
        # Rolling window of entry timestamps (1h).
        self._symbol_hist: dict[str, deque] = defaultdict(deque)
        self._global_hist: deque = deque()
        self.stats = ThrottleStats()

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def check(self, strategy: str, symbol: str, action: str,
              now: Optional[float] = None) -> tuple[bool, str]:
        """Return (ok, reason). Non-entry actions always pass."""
        if not self.enabled:
            return True, "disabled"
        if action not in _ENTRY_ACTIONS:
            return True, "not_entry"

        self.stats.total_evaluated += 1
        now = now if now is not None else time.time()
        symbol = (symbol or "").upper()
        strategy = strategy or ""

        # Prune 1h windows
        self._prune(now)

        # Per-symbol min gap
        if symbol in self._last_symbol_ts:
            last_sym = self._last_symbol_ts[symbol]
            if (now - last_sym) < self.min_per_symbol_s:
                return self._block(f"symbol_gap:{symbol}:{now - last_sym:.0f}s")

        # Per-strategy min gap
        if strategy in self._last_strategy_ts:
            last_str = self._last_strategy_ts[strategy]
            if (now - last_str) < self.min_per_strategy_s:
                return self._block(f"strategy_gap:{strategy}:{now - last_str:.0f}s")

        # Per-symbol hourly cap
        if len(self._symbol_hist[symbol]) >= self.max_per_symbol_h:
            return self._block(f"hourly_cap_symbol:{symbol}")

        # Global hourly cap
        if len(self._global_hist) >= self.max_global_h:
            return self._block("hourly_cap_global")

        return True, "ok"

    def record_entry(self, strategy: str, symbol: str, now: Optional[float] = None) -> None:
        """Call AFTER a trade entry is accepted by all gates."""
        if not self.enabled:
            return
        now = now if now is not None else time.time()
        symbol = (symbol or "").upper()
        self._last_symbol_ts[symbol] = now
        self._last_strategy_ts[strategy] = now
        self._symbol_hist[symbol].append(now)
        self._global_hist.append(now)
        self._prune(now)

    def reset_stats(self) -> None:
        self.stats = ThrottleStats()

    # ----------------------------------------------------------------

    def _prune(self, now: float) -> None:
        cutoff = now - 3600.0
        for sym, d in self._symbol_hist.items():
            while d and d[0] < cutoff:
                d.popleft()
        while self._global_hist and self._global_hist[0] < cutoff:
            self._global_hist.popleft()

    def _block(self, reason: str) -> tuple[bool, str]:
        self.stats.total_blocked += 1
        head = reason.split(":", 1)[0]
        self.stats.blocks_by_reason[head] = self.stats.blocks_by_reason.get(head, 0) + 1
        return False, reason
