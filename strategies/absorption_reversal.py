"""
absorption_reversal.py — Trade absorption proxies (DISABLED BY DEFAULT).

Long if absorption_buy_proxy > threshold  (sellers hitting bids but price holding ↑).
Short if absorption_sell_proxy > threshold (buyers lifting asks but price holding ↓).
"""
from __future__ import annotations

import math
from typing import Optional

from strategies.alpha_pressure_scalper import _AlphaScalperBase


class AbsorptionReversal(_AlphaScalperBase):

    SIGNAL_KEY = "absorption"

    DEFAULT_PARAMS = dict(_AlphaScalperBase.DEFAULT_PARAMS)
    DEFAULT_PARAMS.update(dict(
        threshold=0.0002,  # raw product ti * |r5| — very small by construction
    ))

    def _signal_side_and_score(self, features):
        buy_abs = features.get("absorption_buy_proxy")
        sell_abs = features.get("absorption_sell_proxy")
        if buy_abs is None or sell_abs is None:
            return None, 0.0
        if not (math.isfinite(buy_abs) and math.isfinite(sell_abs)):
            return None, 0.0
        thr = self.config.params["threshold"]
        if buy_abs > thr and buy_abs > sell_abs:
            # normalize: clamp to [0, 1] for the parent class's edge calc
            return "long", min(1.0, float(buy_abs) / max(thr * 5.0, 1e-9))
        if sell_abs > thr and sell_abs > buy_abs:
            return "short", -min(1.0, float(sell_abs) / max(thr * 5.0, 1e-9))
        return None, 0.0
