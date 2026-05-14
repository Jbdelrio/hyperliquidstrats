"""tests/test_execution_planner.py — ExecutionPlanner unit tests."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from execution.execution_planner import ExecutionPlanner
from strategies.base_strategy import StrategyDecision


@dataclass
class _Book:
    best_bid: Optional[float]
    best_ask: Optional[float]

    @property
    def mid(self):
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2.0


def _book(bid=100.0, ask=100.05):
    return _Book(best_bid=bid, best_ask=ask)


def _dec(action="PLACE_BUY", **kw):
    base = dict(
        action=action, symbol="BTC", notional_usd=30.0,
        stop_loss=99.0 if action != "PLACE_SELL" else 101.0,
        take_profit=102.0 if action != "PLACE_SELL" else 98.0,
        expected_edge_bps=20.0, estimated_cost_bps=10.0,
        reward_risk_ratio=2.0,
    )
    base.update(kw)
    return StrategyDecision(**base)


def test_default_order_type_is_maker():
    """Low edge / standard cost → MAKER_SIM is the default."""
    planner = ExecutionPlanner({})
    # edge < 3*cost → MAKER
    d = _dec(expected_edge_bps=10.0, estimated_cost_bps=10.0)
    plan = planner.plan(d, _book())
    assert plan.order_type == "MAKER_SIM"


def test_taker_when_high_edge_low_spread():
    """Edge > 3×cost AND spread < 10bps → TAKER_SIM."""
    planner = ExecutionPlanner({})
    # 5bps spread on a 100 mid; edge 50bps vs cost 10bps → TAKER
    d = _dec(expected_edge_bps=50.0, estimated_cost_bps=10.0)
    bk = _book(bid=100.0, ask=100.05)   # 5bps spread
    plan = planner.plan(d, bk)
    assert plan.order_type == "TAKER_SIM"


def test_taker_blocked_by_wide_spread():
    """Even with high edge, a wide spread forces MAKER."""
    planner = ExecutionPlanner({})
    d = _dec(expected_edge_bps=100.0, estimated_cost_bps=10.0)
    bk = _book(bid=100.0, ask=101.50)   # ~150bps spread
    plan = planner.plan(d, bk)
    assert plan.order_type == "MAKER_SIM"


def test_close_always_taker():
    planner = ExecutionPlanner({})
    d = StrategyDecision(
        action="CLOSE", symbol="BTC",
        notional_usd=30.0,
    )
    plan = planner.plan(d, _book(), is_emergency_close=True)
    assert plan.order_type == "TAKER_SIM"


def test_missing_stop_raises_for_directional():
    planner = ExecutionPlanner({})
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC",
                          notional_usd=30.0, take_profit=102.0)
    with pytest.raises(ValueError):
        planner.plan(d, _book())


def test_missing_tp_raises_for_directional():
    planner = ExecutionPlanner({})
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC",
                          notional_usd=30.0, stop_loss=99.0)
    with pytest.raises(ValueError):
        planner.plan(d, _book())


def test_zero_notional_raises():
    planner = ExecutionPlanner({})
    d = _dec(notional_usd=0.0)
    with pytest.raises(ValueError):
        planner.plan(d, _book())


def test_bad_book_raises():
    planner = ExecutionPlanner({})
    crossed = _Book(best_bid=101.0, best_ask=100.0)
    with pytest.raises(ValueError):
        planner.plan(_dec(), crossed)


def test_plan_sets_correct_max_pending_s():
    cfg = {"paper_sim": {"taker_expire_s": 20, "maker_expire_s": 60}}
    planner = ExecutionPlanner(cfg)
    # MAKER plan
    plan_m = planner.plan(_dec(expected_edge_bps=5.0, estimated_cost_bps=10.0),
                          _book())
    assert plan_m.order_type == "MAKER_SIM"
    assert plan_m.max_pending_s == 60
    # TAKER plan
    plan_t = planner.plan(_dec(expected_edge_bps=60.0, estimated_cost_bps=10.0),
                          _book())
    assert plan_t.order_type == "TAKER_SIM"
    assert plan_t.max_pending_s == 20


def test_signal_id_propagates_to_plan():
    planner = ExecutionPlanner({})
    d = _dec()
    d.signal_id = "abc12345"
    plan = planner.plan(d, _book())
    assert plan.signal_id == "abc12345"


def test_strategy_can_force_order_type():
    planner = ExecutionPlanner({})
    # Force MAKER even with high edge
    d = _dec(expected_edge_bps=200.0, estimated_cost_bps=10.0)
    d.order_type = "MAKER_SIM"
    plan = planner.plan(d, _book())
    assert plan.order_type == "MAKER_SIM"

    d2 = _dec(expected_edge_bps=5.0, estimated_cost_bps=10.0)
    d2.order_type = "TAKER_SIM"
    plan2 = planner.plan(d2, _book())
    assert plan2.order_type == "TAKER_SIM"


def test_taker_limit_price_for_buy_is_best_ask():
    planner = ExecutionPlanner({})
    bk = _book(bid=100.0, ask=100.10)
    d = _dec(action="PLACE_BUY", expected_edge_bps=100.0, estimated_cost_bps=10.0)
    # remove the strategy-provided buy_price so planner derives from book
    d.buy_price = None
    plan = planner.plan(d, bk)
    assert plan.order_type == "TAKER_SIM"
    assert plan.limit_price == 100.10
