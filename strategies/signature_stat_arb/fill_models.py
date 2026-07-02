"""Fill models (§14). Every result records which model produced it.

    simple     : execute at mid + configured cost
    bid_ask    : buy at best ask, sell at best bid
    maker_prob : probabilistic maker fill; P(fill) depends on distance to touch,
                 volatility and order-flow imbalance. Honest approximation — a full
                 L2 queue model is only meaningful with L2 data (not assumed here).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import numpy as np

from .config import ExecutionConfig


@dataclass
class Fill:
    filled_notional: float
    price: float
    is_maker: bool
    model: str
    filled: bool = True


class FillModel:
    def __init__(self, cfg: ExecutionConfig, rng: Optional[np.random.Generator] = None):
        self.cfg = cfg
        self.rng = rng or np.random.default_rng(0)

    def execute(self, side: int, target_notional: float, mid: float,
                bid: Optional[float] = None, ask: Optional[float] = None,
                vol: float = 0.0, ofi: float = 0.0) -> Fill:
        """side: +1 buy, -1 sell. Returns a Fill; maker fills may be partial/none."""
        model = self.cfg.fill_model
        if bid is None or ask is None:
            bid = ask = mid
        if model == "simple":
            return Fill(target_notional, mid, is_maker=False, model="simple")
        if model == "bid_ask":
            px = ask if side > 0 else bid
            return Fill(target_notional, px, is_maker=(self.cfg.mode == "maker_only"),
                        model="bid_ask")
        if model == "maker_prob":
            # rest at the touch; probability of fill shrinks with vol and adverse OFI
            px = bid if side > 0 else ask
            base = 0.6
            p = base * np.exp(-3.0 * abs(vol)) * (1.0 - 0.5 * np.clip(side * ofi, -1, 1))
            p = float(np.clip(p, 0.02, 0.98))
            if self.rng.random() < p:
                # possibly partial
                frac = float(np.clip(self.rng.uniform(0.5, 1.0), 0, 1))
                return Fill(target_notional * frac, px, is_maker=True, model="maker_prob")
            return Fill(0.0, px, is_maker=True, model="maker_prob", filled=False)
        raise ValueError(f"unknown fill model {model}")
