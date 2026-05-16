"""Regression tests for the SL/TP bugs in OBImbalanceScalper + MeanReversionKalman."""
import time
from dataclasses import dataclass
from typing import Optional

import pytest

from strategies.base_strategy import StrategyConfig
from strategies.orderbook_imbalance_scalper import OrderBookImbalanceScalper


@dataclass
class _Book:
    bids: list
    asks: list

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2

    def imbalance(self, n_levels: int = 5) -> float:
        b = sum(sz for _, sz in self.bids[:n_levels])
        a = sum(sz for _, sz in self.asks[:n_levels])
        return (b - a) / (b + a) if (b + a) > 0 else 0.0


def _cfg(**over):
    p = dict(
        imbalance_levels=5,
        imbalance_entry_threshold=0.30,
        min_persistence_updates=2,
        max_spread_bps=20.0,
        require_mid_confirmation=False,
        stop_loss_pct=0.004,
        take_profit_pct=0.003,
        max_hold_seconds=120,
        cooldown_s=15,
        taker_fee_bps=3.0, slippage_bps=2.0,
        min_cost_ratio=0.5,
    )
    p.update(over)
    return StrategyConfig(name="OBI", enabled=True, capital_allocated_usd=500,
                          max_positions=2, max_position_size_usd=250,
                          coins=["BTC"], params=p)


def test_obi_buy_decision_carries_stop_and_tp():
    """Regression: OBImbalanceScalper used to emit decisions WITHOUT
    stop_loss/take_profit, which made SanityCheck reject them as
    'sanity_missing_stop'."""
    s = OrderBookImbalanceScalper(_cfg())
    # 3 ticks with strong buy imbalance (3:1 bid > ask depth)
    for _ in range(3):
        book = _Book(
            bids=[(100.0, 3.0)] * 10,
            asks=[(100.10, 1.0)] * 10,
        )
        d = s.on_orderbook_update("BTC", book, time.time())
    assert d is not None
    assert d.action == "PLACE_BUY"
    assert d.stop_loss is not None and d.stop_loss > 0
    assert d.take_profit is not None and d.take_profit > 0
    # for a long, stop must be below entry and tp above
    assert d.stop_loss < d.buy_price < d.take_profit


def test_obi_sell_decision_carries_stop_and_tp():
    s = OrderBookImbalanceScalper(_cfg())
    for _ in range(3):
        book = _Book(
            bids=[(100.0, 1.0)] * 10,
            asks=[(100.10, 3.0)] * 10,
        )
        d = s.on_orderbook_update("BTC", book, time.time())
    assert d is not None
    assert d.action == "PLACE_SELL"
    assert d.stop_loss is not None and d.take_profit is not None
    # for a short, stop must be above entry and tp below
    assert d.take_profit < d.sell_price < d.stop_loss
