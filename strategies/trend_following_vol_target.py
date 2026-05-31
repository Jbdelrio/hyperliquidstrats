"""
trend_following_vol_target.py — Suivi de tendance 4h/daily, sizing vol-target (PHASE 5).

Intuition : sur barres lentes (4h/daily), les tendances persistent et le mouvement
(dizaines à centaines de bps) dépasse largement le coût (~14 bps RT) → c'est le
chemin le plus robuste pour un edge directionnel net.

Signal : croisement d'EMA. trend = +1 si EMA_fast > EMA_slow, −1 sinon. On détient
toujours dans le sens de la tendance ; on retourne quand elle s'inverse.

Sizing vol-target : notional = capital × (target_vol_bps / max(ATR_bps, floor)),
plafonné. Cela égalise le risque entre coins (un coin calme prend plus de notional
qu'un coin agité). L'edge net par trade (bps) est invariant au sizing — le sizing
sert la gestion du risque de portefeuille, pas l'alpha.

Le cœur du signal vit dans `trend_sign()` (staticmethod), partagé avec l'adaptateur
de backtest (strategy_adapters.ema_cross_run_fn) → zéro dérive recherche/prod.
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
    side: str            # "BUY" | "SELL"
    entry: float
    opened_ts: float


class TrendFollowingVolTargetStrategy(BaseStrategy):
    """EMA-cross trend-following sur barres lentes, sizing vol-target. Voir docstring."""

    DEFAULT_PARAMS = dict(
        tf_minutes=240,                # 4h (mettre 1440 pour daily)
        ema_fast=10,
        ema_slow=30,
        atr_period=14,
        target_vol_bps=200.0,          # vol cible par trade pour le sizing
        vol_floor_bps=30.0,            # plancher ATR (évite notional explosif)
        max_hold_bars=60,              # garde-fou (sortie de secours)
        min_atr_bps=20.0,              # ne pas trader les régimes morts
        maker_only=False,              # tendance = entrée taker honnête
        warmup_from_parquet=False,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(config.params or {})
        config.params = merged
        p = config.params
        self._tf = int(p["tf_minutes"])
        self._fast = int(p["ema_fast"])
        self._slow = int(p["ema_slow"])
        self._atr_n = int(p["atr_period"])
        self._max_hold_s = float(p["max_hold_bars"]) * self._tf * 60.0
        self._agg = {c: BarAggregator(c, self._tf, maxlen=max(300, self._slow + 60))
                     for c in config.coins}
        self._pos: dict[str, _Pos] = {}

    # ── cœur partagé ─────────────────────────────────────────────────────

    @staticmethod
    def _ema(values: list[float], n: int) -> Optional[float]:
        if len(values) < n:
            return None
        a = 2.0 / (n + 1.0)
        e = values[0]
        for v in values[1:]:
            e = a * v + (1 - a) * e
        return e

    @staticmethod
    def trend_sign(closes: list[float], fast: int, slow: int) -> int:
        """+1 (haussier) / −1 (baissier) / 0 (indéterminé) selon EMA_fast vs EMA_slow."""
        if len(closes) < slow:
            return 0
        ef = TrendFollowingVolTargetStrategy._ema(closes, fast)
        es = TrendFollowingVolTargetStrategy._ema(closes, slow)
        if ef is None or es is None:
            return 0
        return 1 if ef > es else -1

    @staticmethod
    def atr_bps(highs, lows, closes, n=14) -> float:
        if len(closes) < n + 1:
            return 0.0
        trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]),
                   abs(lows[i] - closes[i - 1])) for i in range(len(closes) - n, len(closes))]
        atr = sum(trs) / len(trs)
        return (atr / closes[-1] * 1e4) if closes[-1] > 0 else 0.0

    def _vol_target_notional(self, atr_bps: float) -> float:
        p = self.config.params
        floor = max(float(p["vol_floor_bps"]), 1e-6)
        scale = float(p["target_vol_bps"]) / max(atr_bps, floor)
        base = min(self.config.capital_allocated_usd / max(self.config.max_positions, 1),
                   float(self.config.max_position_size_usd))
        return float(min(base * scale, self.config.max_position_size_usd))

    # ── interface ────────────────────────────────────────────────────────

    def data_requirements(self) -> dict:
        return {"orderbook": True, "trades": False, "seconds_features": False,
                "bars": ["1m"], "funding": False, "external_spot": False,
                "warmup_bars": {"1m": (self._slow + 5) * self._tf}}

    def on_orderbook_update(self, symbol, book, ts): return None
    def on_trade_update(self, symbol, trade, ts): return None

    def on_bar_minute(self, symbol, bar, ts) -> Optional[StrategyDecision]:
        agg = self._agg.get(symbol)
        if agg is None:
            return None
        completed = agg.update(bar)
        if completed is None:
            return None
        closes, highs, lows = agg.closes(), agg.highs(), agg.lows()
        if len(closes) < self._slow:
            return None
        p = self.config.params
        sign = self.trend_sign(closes, self._fast, self._slow)
        atr_bps = self.atr_bps(highs, lows, closes, self._atr_n)
        pos = self._pos.get(symbol)

        # En position : retourner si la tendance s'inverse, ou garde-fou de temps.
        if pos is not None:
            held = ts - pos.opened_ts
            flip = (sign < 0 and pos.side == "BUY") or (sign > 0 and pos.side == "SELL")
            if flip or held >= self._max_hold_s:
                return StrategyDecision(action="CLOSE", symbol=symbol,
                                        reason="trend_flip" if flip else "max_hold",
                                        metadata={"pos_id": pos.pos_id})
            return None

        # À plat : entrer dans le sens de la tendance si régime assez actif.
        if sign == 0 or atr_bps < float(p["min_atr_bps"]):
            return None
        notional = self._vol_target_notional(atr_bps)
        close = closes[-1]
        if sign > 0:
            return StrategyDecision(action="PLACE_BUY", symbol=symbol, buy_price=close,
                                    notional_usd=notional, max_hold_seconds=int(self._max_hold_s),
                                    reason=f"trend_long|atr={atr_bps:.0f}bps",
                                    order_type="TAKER_SIM")
        return StrategyDecision(action="PLACE_SELL", symbol=symbol, sell_price=close,
                                notional_usd=notional, max_hold_seconds=int(self._max_hold_s),
                                reason=f"trend_short|atr={atr_bps:.0f}bps",
                                order_type="TAKER_SIM")

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        self._pos[symbol] = _Pos(pos_id=pos_id, side=side, entry=price, opened_ts=ts)
        return None

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._pos.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        agg = self._agg.get(symbol)
        if agg is None:
            return {}
        closes = agg.closes()
        return {"tf_minutes": self._tf, "bars": len(closes),
                "trend": self.trend_sign(closes, self._fast, self._slow) if len(closes) >= self._slow else 0,
                "in_position": symbol in self._pos}
