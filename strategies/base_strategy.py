"""
base_strategy.py — Abstract interface for all Artemisia strategies.
"""
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class StrategyConfig:
    name: str
    enabled: bool = True
    capital_allocated_usd: float = 100.0
    max_positions: int = 1
    max_position_size_usd: float = 100.0
    coins: list = field(default_factory=list)
    params: dict = field(default_factory=dict)
    kill_after_consecutive_losses: int = 5
    suspend_minutes_on_kill: int = 30


@dataclass
class StrategyDecision:
    action: str  # PLACE_QUOTES | PLACE_BUY | PLACE_SELL | CANCEL_QUOTES | CLOSE | SKIP
    symbol: str
    reason: str = ""
    buy_price: Optional[float] = None
    sell_price: Optional[float] = None
    size: Optional[float] = None
    notional_usd: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    max_hold_seconds: Optional[int] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class BarData:
    symbol: str
    ts: float
    open: float
    high: float
    low: float
    close: float
    volume_usd: float
    return_1m: float


class BaseStrategy(ABC):

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        self.config = config
        self.logger = logger
        self.decision_logger = decision_logger
        self._enabled = config.enabled
        self._consecutive_losses = 0
        self._suspended_until = 0.0

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def enabled(self) -> bool:
        return self._enabled and time.time() >= self._suspended_until

    def enable(self) -> None:
        self._enabled = True

    def disable(self) -> None:
        self._enabled = False

    def reset(self) -> None:
        self._consecutive_losses = 0
        self._suspended_until = 0.0

    def update_params(self, params: dict) -> None:
        self.config.params.update(params)

    @abstractmethod
    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        ...

    @abstractmethod
    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        ...

    @abstractmethod
    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        ...

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        """Return {tp_price, stop_price, max_hold_seconds} or None."""
        return None

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        if pnl_net < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.config.kill_after_consecutive_losses:
                self._suspended_until = time.time() + self.config.suspend_minutes_on_kill * 60
                self._consecutive_losses = 0
        else:
            self._consecutive_losses = 0

    def compute_order_notional(self,
                               desired_notional: Optional[float] = None
                               ) -> float:
        """
        Return the notional to use for one position slot.
        Rule: min(capital_per_slot, max_position_size_usd).
        If desired_notional is given, also cap at that value.
        """
        per_slot = (self.config.capital_allocated_usd
                    / max(self.config.max_positions, 1))
        hard_cap = self.config.max_position_size_usd
        if desired_notional is None:
            return min(per_slot, hard_cap)
        return min(desired_notional, per_slot, hard_cap)

    def get_calibration_data(self, symbol: str) -> dict:
        """Live feature values for the Calibration tab."""
        return {}

    def get_stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "consecutive_losses": self._consecutive_losses,
            "suspended_until": self._suspended_until,
            "capital_allocated_usd": self.config.capital_allocated_usd,
            "coins": self.config.coins,
            "params": self.config.params,
        }
