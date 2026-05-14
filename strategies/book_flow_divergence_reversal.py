"""
book_flow_divergence_reversal.py — Trade where taker flow disagrees with
the visible book (DISABLED BY DEFAULT).

Signal :
    alpha_div = trade_imbalance_10s - obi_5

Long if  trade_imbalance_10s > positive_ti AND obi_5 <= 0 AND alpha_div > threshold
Short if trade_imbalance_10s < negative_ti AND obi_5 >= 0 AND alpha_div < -threshold

Hypothesis : visible book is being absorbed / spoofed against, the taker
flow is the truthful side. **Untested**. Default enabled=false.
"""
from __future__ import annotations

import math
from typing import Optional

from strategies.alpha_pressure_scalper import _AlphaScalperBase


class BookFlowDivergenceReversal(_AlphaScalperBase):

    SIGNAL_KEY = "book_flow_divergence"

    DEFAULT_PARAMS = dict(_AlphaScalperBase.DEFAULT_PARAMS)
    DEFAULT_PARAMS.update(dict(
        threshold=0.35,
        positive_ti=0.20,
        negative_ti=-0.20,
    ))

    def _signal_side_and_score(self, features):
        ti = features.get("trade_imbalance_10s")
        obi = features.get("obi_5")
        div = features.get("book_flow_divergence")
        if ti is None or obi is None or div is None:
            return None, 0.0
        if not (math.isfinite(ti) and math.isfinite(obi) and math.isfinite(div)):
            return None, 0.0

        p = self.config.params
        if ti > p["positive_ti"] and obi <= 0 and div > p["threshold"]:
            return "long", float(div)
        if ti < p["negative_ti"] and obi >= 0 and div < -p["threshold"]:
            return "short", float(div)
        return None, 0.0
