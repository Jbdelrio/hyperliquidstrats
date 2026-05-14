"""
seconds_research_strategy.py — No-op strategy used to activate the
per-second feature collection without trading.

The engine instantiates the SecondsFeatureEngine and SecondsFeatureLogger
based on the `seconds_features` config block. This strategy simply
gives the GUI/dashboard something to display ("research is running")
and lets `manager.on_second_features` dispatch arrive at a sink that
also exposes calibration data.

It NEVER returns a non-None StrategyDecision.
"""
from typing import Optional

from strategies.base_strategy import (
    BarData,
    BaseStrategy,
    StrategyConfig,
    StrategyDecision,
)


class SecondsResearchStrategy(BaseStrategy):

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        # Last snapshot seen, keyed by symbol — used by GUI calibration tab.
        self._last_features: dict[str, dict] = {}

    # --- Required BaseStrategy hooks ------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        return None

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        return None

    # --- Seconds hook (this is the whole point) -------------------------

    def on_second_features(self, symbol: str, features: dict, ts: float
                           ) -> Optional[StrategyDecision]:
        # Cache for GUI ; never emit a trade.
        if features and features.get("symbol"):
            self._last_features[symbol] = features
        return None

    # --- Calibration view ----------------------------------------------

    def get_calibration_data(self, symbol: str) -> dict:
        f = self._last_features.get(symbol, {})
        if not f:
            return {}
        # Only expose human-readable fields for the GUI.
        keep = (
            "mid", "spread_bps",
            "obi_1", "obi_3", "obi_5", "obi_10",
            "trade_imbalance_5s", "trade_imbalance_10s", "trade_imbalance_30s",
            "vwap_5s", "vwap_30s", "vwap_slope_5_30",
            "microprice_pressure",
            "r_5s", "r_15s", "r_30s",
            "rv_30s", "rv_60s",
            "liquidity_vacuum",
            "pressure_score_raw",
            "book_flow_divergence",
            "absorption_buy_proxy", "absorption_sell_proxy",
            "book_stale", "enough_data",
        )
        return {k: f.get(k) for k in keep if k in f}
