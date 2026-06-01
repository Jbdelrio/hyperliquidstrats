"""
residual_btc_reversion.py — Réversion du résidu vs BTC (PHASE 5, market-neutral).

Intuition : on régresse le rendement de l'alt sur celui de BTC (beta glissant). Le
RÉSIDU (mouvement idiosyncratique non expliqué par BTC) sur-réagit puis revient à 0.
On FADE le résidu cumulé extrême → version market-neutral de la réversion (le risque
beta-BTC est neutralisé par construction du signal).

Signal (par barre 1h) : beta = cov(r_alt, r_BTC)/var(r_BTC) sur `beta_window` ;
résidu_t = r_alt − beta·r_BTC ; z = somme(résidu sur z_window)/écart-type. z ≥ +zthr
→ SHORT, z ≤ −zthr → LONG. Time-stop `horizon_bars`. BTC doit être dans `coins`.

⚠️ VERDICT HARNAIS (2026-06-01, OOS purgé TOP 20, 1h) : AvgNet −7.79 bps, breadth
7/19, edge déflaté négatif → **NO-GO**. DÉSACTIVÉE par défaut (recherche).
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from strategies.bar_aggregator import BarAggregator
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)
_BTC = "BTC"


@dataclass
class _Pos:
    pos_id: str
    side: str
    opened_ts: float
    max_hold_ts: float


class ResidualBTCReversionStrategy(BaseStrategy):
    DEFAULT_PARAMS = dict(
        tf_minutes=60,
        beta_window=120,
        z_window=48,
        z_entry=2.0,
        horizon_bars=12,
        notional_usd=250.0,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS); merged.update(config.params or {})
        config.params = merged
        p = config.params
        self._tf = int(p["tf_minutes"])
        self._bw = int(p["beta_window"]); self._zw = int(p["z_window"])
        self._zthr = float(p["z_entry"])
        self._hold_s = int(p["horizon_bars"]) * self._tf * 60.0
        self._agg = {c: BarAggregator(c, self._tf, maxlen=self._bw + self._zw + 10)
                     for c in config.coins}
        self._pos: dict[str, _Pos] = {}

    @staticmethod
    def _rets(closes: list[float]) -> list[float]:
        return [math.log(closes[i] / closes[i - 1])
                for i in range(1, len(closes)) if closes[i] > 0 and closes[i - 1] > 0]

    @classmethod
    def residual_z(cls, alt_closes, btc_closes, bw, zw) -> Optional[float]:
        ra, rb = cls._rets(alt_closes), cls._rets(btc_closes)
        n = min(len(ra), len(rb))
        if n < bw + zw:
            return None
        ra, rb = ra[-n:], rb[-n:]
        xb, yb = rb[-bw:], ra[-bw:]
        mx = sum(xb) / bw; my = sum(yb) / bw
        cov = sum((xb[i] - mx) * (yb[i] - my) for i in range(bw)) / bw
        var = sum((x - mx) ** 2 for x in xb) / bw
        beta = cov / var if var > 0 else 0.0
        resid = [ra[-zw + i] - beta * rb[-zw + i] for i in range(zw)]
        cum = sum(resid)
        mr = cum / zw
        sd = math.sqrt(sum((r - mr) ** 2 for r in resid) / zw) * math.sqrt(zw)
        return (cum / sd) if sd > 0 else None

    def data_requirements(self) -> dict:
        return {"orderbook": True, "trades": False, "seconds_features": False,
                "bars": ["1m"], "funding": False, "external_spot": False,
                "warmup_bars": {"1m": (self._bw + self._zw + 2) * self._tf}}

    def on_orderbook_update(self, s, b, t): return None
    def on_trade_update(self, s, tr, t): return None

    def on_bar_minute(self, symbol, bar, ts) -> Optional[StrategyDecision]:
        agg = self._agg.get(symbol)
        if agg is None or agg.update(bar) is None:
            return None
        if symbol == _BTC:
            return None                            # BTC = référence, pas tradé
        pos = self._pos.get(symbol)
        if pos is not None:
            if ts >= pos.max_hold_ts:
                return StrategyDecision(action="CLOSE", symbol=symbol,
                                        reason="resid_time_exit", metadata={"pos_id": pos.pos_id})
            return None
        btc = self._agg.get(_BTC)
        if btc is None or len(self._pos) >= int(self.config.max_positions):
            return None
        z = self.residual_z(agg.closes(), btc.closes(), self._bw, self._zw)
        if z is None:
            return None
        sig = -1 if z >= self._zthr else (1 if z <= -self._zthr else 0)
        if sig == 0:
            return None
        notional = float(self.config.params["notional_usd"])
        close = agg.closes()[-1]
        if sig > 0:
            return StrategyDecision(action="PLACE_BUY", symbol=symbol, buy_price=close,
                                    notional_usd=notional, max_hold_seconds=int(self._hold_s),
                                    reason=f"resid_long|z={z:.2f}", order_type="TAKER_SIM")
        return StrategyDecision(action="PLACE_SELL", symbol=symbol, sell_price=close,
                                notional_usd=notional, max_hold_seconds=int(self._hold_s),
                                reason=f"resid_short|z={z:.2f}", order_type="TAKER_SIM")

    def on_fill(self, symbol, side, price, size, ts, pos_id=""):
        self._pos[symbol] = _Pos(pos_id, side, ts, ts + self._hold_s)
        return None

    def on_position_closed(self, symbol, pnl_net, exit_reason):
        self._pos.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        agg = self._agg.get(symbol); btc = self._agg.get(_BTC)
        z = None
        if agg and btc and symbol != _BTC:
            z = self.residual_z(agg.closes(), btc.closes(), self._bw, self._zw)
        return {"tf_minutes": self._tf, "resid_z": round(z, 3) if z is not None else None,
                "in_position": symbol in self._pos}
