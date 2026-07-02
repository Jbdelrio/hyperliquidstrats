"""Transaction-cost model (§13). Generic form C(v) = a|v| + b v^2 + c·1{v!=0}.

Costs are configurable per exchange/symbol via :class:`CostConfig`; nothing about
"current" fees is hard-coded. All costs are returned in USD for a given traded
notional so the backtest can decompose net PnL (§7 costs page).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict
import numpy as np

from .config import CostConfig


@dataclass
class FillCosts:
    fee: float = 0.0
    spread: float = 0.0
    slippage: float = 0.0
    impact: float = 0.0
    fixed: float = 0.0
    latency: float = 0.0

    @property
    def total(self) -> float:
        return self.fee + self.spread + self.slippage + self.impact + self.fixed + self.latency

    def add(self, other: "FillCosts") -> "FillCosts":
        return FillCosts(self.fee + other.fee, self.spread + other.spread,
                         self.slippage + other.slippage, self.impact + other.impact,
                         self.fixed + other.fixed, self.latency + other.latency)


class CostModel:
    def __init__(self, cfg: CostConfig):
        self.cfg = cfg

    def trade_cost(self, traded_notional_usd: float, is_maker: bool,
                   book_spread_bps: float = 0.0) -> FillCosts:
        """Cost of executing ``traded_notional_usd`` (absolute) on one leg."""
        n = abs(traded_notional_usd)
        if n <= 0:
            return FillCosts()
        c = self.cfg
        fee_bps = c.maker_fee_bps if is_maker else c.taker_fee_bps
        fee = n * fee_bps / 1e4
        # taker crosses half the book spread; maker earns it (approximation)
        spread = n * (book_spread_bps / 2.0) / 1e4 * (1.0 if not is_maker else -1.0)
        slippage = n * c.default_slippage_bps / 1e4 if not is_maker else 0.0
        impact = c.temporary_impact_coefficient * (n ** 2)
        fixed = c.fixed_cost_per_trade_usd
        latency = n * c.latency_penalty_bps / 1e4 if not is_maker else 0.0
        return FillCosts(fee=fee, spread=spread, slippage=slippage,
                         impact=impact, fixed=fixed, latency=latency)

    def funding_cost(self, held_notional_usd: float) -> float:
        """Funding on the net held notional over one funding interval."""
        return held_notional_usd * self.cfg.funding_bps_per_interval / 1e4
