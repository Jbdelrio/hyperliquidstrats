"""
funding_extreme_reversal.py — Réversion sur funding extrême (PHASE 5).

Intuition : le funding mesure le positionnement. Quand il atteint un percentile
historique EXTRÊME, le positionnement est saturé (foule longue qui sur-paie, ou
foule courte) → squeeze probable → réversion du PRIX. C'est du SENTIMENT quantifié,
pas du carry (on parie sur le prix, pas sur l'encaissement du funding).

Convention HL : funding f horaire. f>0 → les longs paient (foule longue) → on FADE
en SHORT. f<0 → foule courte → LONG. Seuils = percentiles glissants (causal).

Signal partagé via `signal_from_funding()` (staticmethod) → réutilisé par
l'adaptateur de backtest. Validation OOS requise avant toute activation.
"""
from __future__ import annotations

import logging
from typing import Optional

from strategies.base_strategy import BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


class FundingExtremeReversalStrategy(BaseStrategy):
    DEFAULT_PARAMS = dict(
        window_bars=240,          # fenêtre de percentile (≈10j en 1h)
        hi_pct=0.90, lo_pct=0.10, # seuils de funding extrême
        horizon_bars=6,           # durée de détention (h)
        min_abs_funding_bps=0.5,  # plancher : ignorer le funding ~0
        maker_only=False,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS); merged.update(config.params or {})
        config.params = merged

    @staticmethod
    def signal_from_funding(f_now: float, f_hist: list[float],
                            hi_pct: float, lo_pct: float, min_abs: float) -> int:
        """+1 LONG (foule courte, f bas) / −1 SHORT (foule longue, f haut) / 0."""
        if len(f_hist) < 30 or abs(f_now) * 1e4 < min_abs:
            return 0
        import numpy as np
        hi = float(np.quantile(f_hist, hi_pct))
        lo = float(np.quantile(f_hist, lo_pct))
        if f_now >= hi:
            return -1
        if f_now <= lo:
            return 1
        return 0

    def data_requirements(self) -> dict:
        return {"orderbook": True, "trades": False, "seconds_features": False,
                "bars": ["1h"], "funding": True, "external_spot": False,
                "warmup_bars": {"1h": 240}}

    def on_orderbook_update(self, s, b, t): return None
    def on_trade_update(self, s, tr, t): return None
    def on_bar_minute(self, s, bar, t): return None   # live wiring du funding séparé
