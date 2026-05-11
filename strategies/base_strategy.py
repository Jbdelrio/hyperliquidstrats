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
        self._killed = False

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def enabled(self) -> bool:
        return self._enabled and time.time() >= self._suspended_until

    def state(self, now: float = None) -> str:
        """Return canonical runtime state: ACTIVE | SUSPENDED | DISABLED | KILLED."""
        now = now or time.time()
        if self._killed:
            return "KILLED"
        if not self._enabled:
            return "DISABLED"
        if now < self._suspended_until:
            return "SUSPENDED"
        return "ACTIVE"

    def enable(self) -> None:
        self._enabled = True
        self._killed = False

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

    def estimate_trade_economics(
        self,
        entry: float,
        tp: float,
        sl: float,
        notional: float,
        side: str,
        fee_bps: float = 3.0,
        slippage_bps: float = 4.0,
    ) -> dict:
        """Estimate expected P&L after round-trip fees and slippage."""
        if side == "long":
            tp_move = (tp - entry) / entry if entry > 0 else 0.0
            sl_move = (sl - entry) / entry if entry > 0 else 0.0
        else:
            tp_move = (entry - tp) / entry if entry > 0 else 0.0
            sl_move = (entry - sl) / entry if entry > 0 else 0.0

        gross_tp = tp_move * notional
        round_trip_cost = 2 * ((fee_bps + slippage_bps) / 10_000.0) * notional
        expected_net = gross_tp - round_trip_cost
        risk_usd = abs(sl_move * notional) + round_trip_cost
        rr = expected_net / risk_usd if risk_usd > 0 else 0.0

        return {
            "gross_profit_usd":      gross_tp,
            "estimated_fees_usd":    round_trip_cost,
            "expected_net_profit_usd": expected_net,
            "risk_usd":              risk_usd,
            "reward_risk_ratio":     rr,
            "tp_pct":                tp_move * 100,
            "sl_pct":                sl_move * 100,
        }

    def passes_min_edge_filter(
        self,
        entry: float,
        tp: float,
        sl: float,
        notional: float,
        side: str,
        min_net_profit: float = 3.0,
        min_rr: float = 1.4,
        fee_bps: float = 3.0,
        slippage_bps: float = 4.0,
    ) -> "tuple[bool, str, dict]":
        """Return (passes, blocked_reason, economics_dict)."""
        econ = self.estimate_trade_economics(
            entry, tp, sl, notional, side, fee_bps, slippage_bps)
        net = econ["expected_net_profit_usd"]
        rr  = econ["reward_risk_ratio"]
        if net < min_net_profit:
            return False, f"net_too_low:{net:.2f}<{min_net_profit:.2f}", econ
        if rr < min_rr:
            return False, f"rr_too_low:{rr:.2f}<{min_rr:.2f}", econ
        return True, "", econ

    def get_stats(self) -> dict:
        now = time.time()
        return {
            "enabled":               self.enabled,
            "state":                 self.state(now),
            "raw_enabled":           self._enabled,
            "effective_enabled":     self.enabled,
            "consecutive_losses":    self._consecutive_losses,
            "suspended_until":       self._suspended_until,
            "suspended_remaining_s": max(0.0, self._suspended_until - now),
            "capital_allocated_usd": self.config.capital_allocated_usd,
            "coins":                 self.config.coins,
            "params":                self.config.params,
        }
