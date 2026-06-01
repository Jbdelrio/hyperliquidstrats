"""
liquidation_cascade_reversal.py — Réversion de cascade de liquidation (PHASE 5).

Intuition : une bougie au range anormal (> k·ATR) avec un spike de volume est un
proxy de cascade de liquidations (stops/liquidations en chaîne). Après l'épuisement
de la cascade, le prix tend à reverter une partie de la mèche → on FADE la bougie.

Signal (par barre 15m) : range = high−low ; si range > k·ATR(14) ET volume >
m·volume_moyen(20) → fade (LONG si bougie rouge, SHORT si verte). Time-stop.

⚠️ VERDICT HARNAIS (2026-06-01, OOS purgé TOP 20, 15m) : AvgNet −6.13 bps,
breadth 8/20, edge déflaté négatif → **NO-GO**. DÉSACTIVÉE par défaut (recherche).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from strategies.bar_aggregator import BarAggregator
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


@dataclass
class _Pos:
    pos_id: str
    side: str
    opened_ts: float
    max_hold_ts: float


class LiquidationCascadeReversalStrategy(BaseStrategy):
    DEFAULT_PARAMS = dict(
        tf_minutes=15,
        range_atr_mult=3.0,     # range > k·ATR
        vol_mult=3.0,           # volume > m·moyenne
        atr_period=14,
        vol_window=20,
        horizon_bars=4,         # détention (≈1h en 15m)
        notional_usd=250.0,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS); merged.update(config.params or {})
        config.params = merged
        p = config.params
        self._tf = int(p["tf_minutes"])
        self._k = float(p["range_atr_mult"]); self._m = float(p["vol_mult"])
        self._atr_n = int(p["atr_period"]); self._vw = int(p["vol_window"])
        self._hold_s = int(p["horizon_bars"]) * self._tf * 60.0
        self._agg = {c: BarAggregator(c, self._tf, maxlen=max(self._atr_n, self._vw) + 10)
                     for c in config.coins}
        self._pos: dict[str, _Pos] = {}

    @staticmethod
    def cascade_signal(opens, highs, lows, closes, vols, k, m, atr_n, vw) -> int:
        """+1 LONG (fade bougie rouge) / −1 SHORT (fade verte) / 0."""
        if len(closes) < max(atr_n, vw) + 1:
            return 0
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
               for i in range(len(closes) - atr_n, len(closes))]
        atr = sum(trs) / atr_n
        vavg = sum(vols[-vw:]) / vw
        rng = highs[-1] - lows[-1]
        if atr <= 0 or vavg <= 0:
            return 0
        if rng > k * atr and vols[-1] > m * vavg:
            return 1 if closes[-1] < opens[-1] else -1
        return 0

    def data_requirements(self) -> dict:
        return {"orderbook": True, "trades": False, "seconds_features": False,
                "bars": ["1m"], "funding": False, "external_spot": False,
                "warmup_bars": {"1m": (max(self._atr_n, self._vw) + 2) * self._tf}}

    def on_orderbook_update(self, s, b, t): return None
    def on_trade_update(self, s, tr, t): return None

    def on_bar_minute(self, symbol, bar, ts) -> Optional[StrategyDecision]:
        agg = self._agg.get(symbol)
        if agg is None or agg.update(bar) is None:
            return None
        pos = self._pos.get(symbol)
        if pos is not None:
            if ts >= pos.max_hold_ts:
                return StrategyDecision(action="CLOSE", symbol=symbol,
                                        reason="cascade_time_exit", metadata={"pos_id": pos.pos_id})
            return None
        if len(self._pos) >= int(self.config.max_positions):
            return None
        sig = self.cascade_signal(agg.opens(), agg.highs(), agg.lows(), agg.closes(),
                                  agg.volumes(), self._k, self._m, self._atr_n, self._vw)
        if sig == 0:
            return None
        notional = float(self.config.params["notional_usd"])
        close = agg.closes()[-1]
        if sig > 0:
            return StrategyDecision(action="PLACE_BUY", symbol=symbol, buy_price=close,
                                    notional_usd=notional, max_hold_seconds=int(self._hold_s),
                                    reason="cascade_fade_long", order_type="TAKER_SIM")
        return StrategyDecision(action="PLACE_SELL", symbol=symbol, sell_price=close,
                                notional_usd=notional, max_hold_seconds=int(self._hold_s),
                                reason="cascade_fade_short", order_type="TAKER_SIM")

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        self._pos[symbol] = _Pos(pos_id, side, ts, ts + self._hold_s)
        return None

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._pos.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        agg = self._agg.get(symbol)
        return {"tf_minutes": self._tf, "bars": len(agg) if agg else 0,
                "in_position": symbol in self._pos}
