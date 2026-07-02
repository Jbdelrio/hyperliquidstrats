"""Two-leg execution manager (§15).

A spread trade is two legs (leg-1 long, leg-2 short by the hedge ratio). This
manager converts a target spread inventory into per-leg target notionals, tracks
the realised hedge imbalance, and reports legging risk. It is deliberately simple
(bar-level, backtest-only) but records the quantities the spec asks for so the
GUI can display real hedge-error series.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List
import numpy as np

from .costs import CostModel, FillCosts
from .fill_models import FillModel


@dataclass
class LegState:
    notional: float = 0.0            # signed USD position on the leg


@dataclass
class TwoLegExecutionManager:
    cost_model: CostModel
    fill_model: FillModel
    leg1: LegState = field(default_factory=LegState)
    leg2: LegState = field(default_factory=LegState)
    imbalance_bars: int = 0
    desync_incidents: int = 0
    legging_pnl: float = 0.0

    def rebalance(self, target_spread_notional: float, beta: float,
                  mid1: float, mid2: float, book_spread_bps: float = 0.0,
                  vol: float = 0.0, ofi: float = 0.0) -> Dict:
        """Move both legs toward the target. leg1 target = Q, leg2 = -beta*Q.

        Returns a dict with executed notionals, hedge error and costs for the bar.
        """
        tgt1 = target_spread_notional
        tgt2 = -beta * target_spread_notional
        c1 = self._exec_leg(self.leg1, tgt1, mid1, +1, book_spread_bps, vol, ofi)
        c2 = self._exec_leg(self.leg2, tgt2, mid2, -1, book_spread_bps, vol, ofi)
        costs = c1.add(c2)
        # residual net dollar exposure (0 = perfectly $-hedged legs)
        hedge_err = abs(self.leg1.notional + self.leg2.notional)
        if hedge_err > 0.25 * max(abs(self.leg1.notional), 1.0):
            self.imbalance_bars += 1
            self.desync_incidents += 1
        return {
            "target_leg1": tgt1, "target_leg2": tgt2,
            "notional_leg1": self.leg1.notional, "notional_leg2": self.leg2.notional,
            "hedge_ratio_target": beta,
            "hedge_ratio_real": (-self.leg2.notional / self.leg1.notional) if abs(self.leg1.notional) > 1e-9 else np.nan,
            "dollar_imbalance": self.leg1.notional + self.leg2.notional,
            "costs": costs,
        }

    def _exec_leg(self, leg: LegState, target: float, mid: float, side_hint: int,
                  book_spread_bps: float, vol: float, ofi: float) -> FillCosts:
        delta = target - leg.notional
        if abs(delta) < 1e-9:
            return FillCosts()
        side = 1 if delta > 0 else -1
        fill = self.fill_model.execute(side, abs(delta), mid, vol=vol, ofi=ofi)
        if not fill.filled or fill.filled_notional <= 0:
            return FillCosts()
        executed = side * fill.filled_notional
        leg.notional += executed
        return self.cost_model.trade_cost(fill.filled_notional, fill.is_maker, book_spread_bps)

    def gross_exposure(self) -> float:
        return abs(self.leg1.notional) + abs(self.leg2.notional)

    def net_exposure(self) -> float:
        return self.leg1.notional + self.leg2.notional
