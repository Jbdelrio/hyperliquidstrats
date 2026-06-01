"""
cross_sectional_reversal.py — Réversion transversale (PHASE 5).

Intuition : à court terme, les coins qui ont le plus monté (resp. baissé) par
rapport au panier tendent à REVENIR vers la moyenne transversale. On SHORTE le
quartile haut du rendement récent et on LONGE le quartile bas (inverse du momentum
transversal de momentum_long_short).

Signal (par barre, lente, ex. 1h) : rendement sur `lookback_bars`, rang percentile
transversal `rk ∈ [0,1]`. rk ≥ 1−q → SHORT ; rk ≤ q → LONG. Détention `horizon_bars`.

⚠️ VERDICT HARNAIS (2026-06-01, OOS purgé TOP 20, 1h) : AvgNet −9.05 bps, breadth
7/20, edge déflaté négatif → **NO-GO**. DÉSACTIVÉE par défaut (recherche). Ne pas
activer sans re-validation GO (cf. reports/SELECTION_GATE.md).
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


class CrossSectionalReversalStrategy(BaseStrategy):
    DEFAULT_PARAMS = dict(
        tf_minutes=60,
        lookback_bars=4,        # rendement transversal sur 4h
        horizon_bars=12,        # détention 12h
        quantile=0.25,          # quartiles
        min_universe=4,         # nb mini de coins pour ranker
        notional_usd=250.0,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS); merged.update(config.params or {})
        config.params = merged
        p = config.params
        self._tf = int(p["tf_minutes"])
        self._look = int(p["lookback_bars"])
        self._hold_s = int(p["horizon_bars"]) * self._tf * 60.0
        self._q = float(p["quantile"])
        self._agg = {c: BarAggregator(c, self._tf, maxlen=self._look + 5) for c in config.coins}
        self._last_ret: dict[str, float] = {}     # rendement lookback courant par coin
        self._pos: dict[str, _Pos] = {}

    @staticmethod
    def rank_signal(my_ret: float, all_rets: list[float], q: float) -> int:
        """+1 LONG (bas quantile) / −1 SHORT (haut quantile) / 0."""
        if len(all_rets) < 3:
            return 0
        below = sum(1 for r in all_rets if r < my_ret)
        rk = below / (len(all_rets) - 1) if len(all_rets) > 1 else 0.5
        if rk >= 1 - q:
            return -1
        if rk <= q:
            return 1
        return 0

    def data_requirements(self) -> dict:
        return {"orderbook": True, "trades": False, "seconds_features": False,
                "bars": ["1m"], "funding": False, "external_spot": False,
                "warmup_bars": {"1m": (self._look + 2) * self._tf}}

    def on_orderbook_update(self, s, b, t): return None
    def on_trade_update(self, s, tr, t): return None

    def on_bar_minute(self, symbol, bar, ts) -> Optional[StrategyDecision]:
        agg = self._agg.get(symbol)
        if agg is None or agg.update(bar) is None:
            return None
        closes = agg.closes()
        if len(closes) >= self._look + 1 and closes[-1 - self._look] > 0:
            self._last_ret[symbol] = (closes[-1] - closes[-1 - self._look]) / closes[-1 - self._look]

        pos = self._pos.get(symbol)
        if pos is not None:
            if ts >= pos.max_hold_ts:
                return StrategyDecision(action="CLOSE", symbol=symbol,
                                        reason="xs_time_exit", metadata={"pos_id": pos.pos_id})
            return None
        if symbol not in self._last_ret or len(self._pos) >= int(self.config.max_positions):
            return None
        others = [r for c, r in self._last_ret.items() if c != symbol]
        if len(others) + 1 < int(self.config.params["min_universe"]):
            return None
        sig = self.rank_signal(self._last_ret[symbol], others + [self._last_ret[symbol]], self._q)
        if sig == 0:
            return None
        notional = float(self.config.params["notional_usd"])
        close = closes[-1]
        if sig > 0:
            return StrategyDecision(action="PLACE_BUY", symbol=symbol, buy_price=close,
                                    notional_usd=notional, max_hold_seconds=int(self._hold_s),
                                    reason="xs_reversal_long", order_type="TAKER_SIM")
        return StrategyDecision(action="PLACE_SELL", symbol=symbol, sell_price=close,
                                notional_usd=notional, max_hold_seconds=int(self._hold_s),
                                reason="xs_reversal_short", order_type="TAKER_SIM")

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        self._pos[symbol] = _Pos(pos_id, side, ts, ts + self._hold_s)
        return None

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._pos.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        return {"tf_minutes": self._tf, "last_ret": round(self._last_ret.get(symbol, 0.0), 5),
                "universe": len(self._last_ret), "in_position": symbol in self._pos}
