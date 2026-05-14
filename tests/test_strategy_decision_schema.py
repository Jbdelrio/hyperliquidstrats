"""tests/test_strategy_decision_schema.py — Enriched StrategyDecision schema."""
from __future__ import annotations

from strategies.base_strategy import StrategyDecision


def test_signal_id_auto_generated():
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC")
    assert d.signal_id
    assert isinstance(d.signal_id, str)
    assert len(d.signal_id) == 8


def test_signal_id_unique_across_decisions():
    a = StrategyDecision(action="PLACE_BUY", symbol="BTC")
    b = StrategyDecision(action="PLACE_BUY", symbol="ETH")
    assert a.signal_id != b.signal_id


def test_default_values_safe():
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC")
    # All new numeric fields default to 0
    assert d.confidence == 0.0
    assert d.expected_edge_bps == 0.0
    assert d.expected_net_profit_usd == 0.0
    assert d.estimated_cost_bps == 0.0
    assert d.risk_usd == 0.0
    # Routing default is "auto" (empty string) — planner decides
    assert d.order_type == ""
    assert d.time_in_force == "GTC"


def test_reward_risk_ratio_default_zero():
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC")
    assert d.reward_risk_ratio == 0.0


def test_requires_llm_review_default_false():
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC")
    assert d.requires_llm_review is False


def test_strategy_family_default_empty():
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC")
    assert d.strategy_family == ""


def test_legacy_fields_unchanged():
    """Backward compatibility: existing fields keep their old defaults."""
    d = StrategyDecision(action="PLACE_BUY", symbol="BTC")
    assert d.reason == ""
    assert d.buy_price is None
    assert d.sell_price is None
    assert d.size is None
    assert d.notional_usd is None
    assert d.stop_loss is None
    assert d.take_profit is None
    assert d.max_hold_seconds is None
    assert d.metadata == {}


def test_can_construct_with_all_new_fields():
    d = StrategyDecision(
        action="PLACE_BUY", symbol="BTC",
        notional_usd=30.0,
        stop_loss=99.0, take_profit=102.0,
        confidence=0.8,
        expected_edge_bps=30.0,
        expected_net_profit_usd=0.20,
        estimated_cost_bps=9.0,
        risk_usd=0.30,
        reward_risk_ratio=2.0,
        order_type="MAKER_SIM",
        time_in_force="IOC",
        signal_id="custom01",
        strategy_family="momentum",
        requires_llm_review=True,
    )
    assert d.order_type == "MAKER_SIM"
    assert d.time_in_force == "IOC"
    assert d.signal_id == "custom01"
    assert d.strategy_family == "momentum"
    assert d.requires_llm_review is True
    assert d.confidence == 0.8
