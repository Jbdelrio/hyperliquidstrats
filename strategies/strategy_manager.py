"""
strategy_manager.py — Routes events to all active strategies and consolidates decisions.
"""
import logging
from typing import Optional

from strategies.base_strategy import BarData, BaseStrategy, StrategyDecision

log = logging.getLogger(__name__)


class StrategyManager:

    def __init__(self, config: dict, decision_logger, kill_switch):
        self.strategies: dict[str, BaseStrategy] = {}
        self.kill_switch = kill_switch
        self.decision_logger = decision_logger
        self._global_cfg = config

    def register(self, strategy: BaseStrategy) -> None:
        self.strategies[strategy.name] = strategy
        log.info("Strategy registered: %s (enabled=%s, coins=%s)",
                 strategy.name, strategy.enabled, strategy.config.coins)

    def get(self, name: str) -> Optional[BaseStrategy]:
        return self.strategies.get(name)

    # ------------------------------------------------------------------
    # Event dispatch
    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> list:
        """Returns list of (strat_name, StrategyDecision) for non-SKIP decisions."""
        results = []
        for name, strat in self.strategies.items():
            if not strat.enabled:
                continue
            if symbol not in strat.config.coins:
                continue
            if self.kill_switch.is_killed():
                continue
            try:
                d = strat.on_orderbook_update(symbol, book, ts)
                if d is not None and d.action != "SKIP":
                    results.append((name, d))
            except Exception as e:
                log.error("Strategy %s error on_orderbook_update %s: %s", name, symbol, e)
        return results

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        for name, strat in self.strategies.items():
            if not strat.enabled:
                continue
            if symbol not in strat.config.coins:
                continue
            try:
                strat.on_trade_update(symbol, trade, ts)
            except Exception as e:
                log.error("Strategy %s error on_trade_update %s: %s", name, symbol, e)

    def on_second_features(self, symbol: str, features: dict, ts: float) -> list:
        """Dispatch a SecondsFeatureEngine snapshot to enabled strategies.

        Strategies that don't override `on_second_features` return None
        (default in BaseStrategy) so they're skipped here without cost.
        """
        results = []
        for name, strat in self.strategies.items():
            if not strat.enabled:
                continue
            if symbol not in strat.config.coins:
                continue
            if self.kill_switch.is_killed():
                continue
            try:
                d = strat.on_second_features(symbol, features, ts)
                if d is not None and d.action != "SKIP":
                    results.append((name, d))
            except Exception as e:
                log.error("Strategy %s error on_second_features %s: %s", name, symbol, e)
        return results

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> list:
        """Returns list of (strat_name, StrategyDecision) for decisions from bar events."""
        results = []
        for name, strat in self.strategies.items():
            if not strat.enabled:
                continue
            if symbol not in strat.config.coins:
                continue
            try:
                d = strat.on_bar_minute(symbol, bar, ts)
                if d is not None and d.action != "SKIP":
                    results.append((name, d))
            except Exception as e:
                log.error("Strategy %s error on_bar_minute %s: %s", name, symbol, e)
        return results

    def check_position_exits(self, symbol: str, book, ts: float) -> list:
        """Returns list of (strat_name, StrategyDecision(CLOSE)) for exits."""
        results = []
        for name, strat in self.strategies.items():
            if symbol not in strat.config.coins:
                continue
            try:
                d = strat.check_position_exits(symbol, book, ts)
                if d is not None and d.action == "CLOSE":
                    results.append((name, d))
            except Exception as e:
                log.error("Strategy %s error check_position_exits %s: %s", name, symbol, e)
        return results

    # ------------------------------------------------------------------
    # Control API (called from engine's control loop)
    # ------------------------------------------------------------------

    def control(self, name: str, action: str, **kwargs) -> dict:
        if name not in self.strategies:
            return {"ok": False, "error": f"unknown strategy: {name}"}
        strat = self.strategies[name]
        try:
            if action == "enable":
                strat.enable()
            elif action == "disable":
                strat.disable()
            elif action == "reset":
                strat.reset()
            elif action == "update_params":
                strat.update_params(kwargs.get("params", {}))
            elif action == "set_capital":
                strat.config.capital_allocated_usd = float(kwargs["capital_usd"])
            elif action == "set_coins":
                strat.config.coins = list(kwargs["coins"])
            else:
                return {"ok": False, "error": f"unknown action: {action}"}
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Status / calibration (for GUI and runtime status file)
    # ------------------------------------------------------------------

    def get_status(self) -> list:
        import time
        now = time.time()
        return [
            {
                "name":                  s.name,
                "state":                 s.state(now),
                "enabled":               s.enabled,
                "raw_enabled":           s._enabled,
                "effective_enabled":     s.enabled,
                "consecutive_losses":    s._consecutive_losses,
                "suspended_until":       s._suspended_until,
                "suspended_remaining_s": max(0.0, s._suspended_until - now),
                "capital_allocated_usd": s.config.capital_allocated_usd,
                "coins":                 s.config.coins,
                "params":               s.config.params,
            }
            for s in self.strategies.values()
        ]

    def get_calibration_data(self) -> dict:
        result = {}
        for name, strat in self.strategies.items():
            if not strat.enabled:
                continue
            strat_data = {}
            for coin in strat.config.coins:
                try:
                    strat_data[coin] = strat.get_calibration_data(coin)
                except Exception:
                    strat_data[coin] = {}
            result[name] = strat_data
        return result
