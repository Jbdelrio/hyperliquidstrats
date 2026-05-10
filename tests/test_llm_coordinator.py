"""
tests/test_llm_coordinator.py — Unit tests for coordinator and signal combination.
"""
import os
import dataclasses
import pytest

os.environ.setdefault("LLM_ENABLED", "false")

from llm_agents.schemas import AgentForecast, LLMDecision, MarketSnapshot
from llm_agents.coordinator import LLMCoordinator, combine_strategy_and_llm, _action_to_direction


def _make_forecast(agent_name="A", prob_up=0.5, prob_down=0.5,
                   action="NO_TRADE", confidence="low", flags=None):
    return AgentForecast(
        agent_name=agent_name, prob_up=prob_up, prob_down=prob_down,
        confidence=confidence, horizon_minutes=60, reasoning="test",
        risk_flags=flags or [], suggested_action=action,
    )


def _make_decision(symbol="BTC", action="LONG", allow=True, mult=1.0, flags=None):
    return LLMDecision(
        enabled=True, architecture="test", symbol=symbol,
        horizon_minutes=60, final_prob_up=0.6, final_prob_down=0.4,
        final_confidence="high", final_action=action,
        allow_trade=allow, max_risk_multiplier=mult,
        reason="test", risk_flags=flags or [],
    )


def _make_strategy_decision(action="PLACE_BUY", symbol="BTC", notional=100.0):
    from strategies.base_strategy import StrategyDecision
    return StrategyDecision(action=action, symbol=symbol, notional_usd=notional)


# ── Coordinator aggregation ─────────────────────────────────────────────────

def test_coordinator_uses_dummy_provider():
    coord = LLMCoordinator()
    assert coord.provider is not None


def test_coordinator_independent_ensemble_no_trade_on_low_prob():
    """With dummy provider (50/50), should produce NO_TRADE."""
    coord = LLMCoordinator()
    snap = MarketSnapshot(symbol="BTC", timestamp="2026-01-01T00:00:00Z")
    dec = coord._independent_ensemble(snap)
    assert dec.final_action == "NO_TRADE"
    assert dec.allow_trade is False


def test_coordinator_aggregate_high_prob_up():
    coord = LLMCoordinator()
    forecasts = [
        _make_forecast("A", 0.65, 0.35, "LONG", "high"),
        _make_forecast("B", 0.63, 0.37, "LONG", "medium"),
        _make_forecast("RiskManagerAgent", 0.62, 0.38, "LONG", "medium"),
        _make_forecast("D", 0.61, 0.39, "LONG", "medium"),
    ]
    dec = coord._aggregate("BTC", forecasts)
    assert dec.final_action == "LONG"
    assert dec.allow_trade is True


def test_coordinator_risk_manager_veto():
    """RiskManagerAgent saying NO_TRADE should block regardless of other agents."""
    import os
    os.environ["LLM_REQUIRE_RISK_APPROVAL"] = "true"
    coord = LLMCoordinator()
    forecasts = [
        _make_forecast("PriceActionAgent", 0.8, 0.2, "LONG", "high"),
        _make_forecast("MicrostructureAgent", 0.75, 0.25, "LONG", "high"),
        _make_forecast("RiskManagerAgent", 0.5, 0.5, "NO_TRADE", "low",
                       flags=["high_spread"]),
    ]
    dec = coord._aggregate("BTC", forecasts)
    assert dec.allow_trade is False


def test_coordinator_blocking_flags():
    coord = LLMCoordinator()
    forecasts = [
        _make_forecast("A", 0.7, 0.3, "LONG", "high", flags=["extreme_volatility"]),
    ]
    dec = coord._aggregate("BTC", forecasts)
    assert dec.allow_trade is False


def test_coordinator_disagreement_reduces_multiplier():
    coord = LLMCoordinator()
    forecasts = [
        _make_forecast("A", 0.9, 0.1, "LONG",  "high"),
        _make_forecast("B", 0.3, 0.7, "SHORT", "high"),
        _make_forecast("RiskManagerAgent", 0.6, 0.4, "LONG", "medium"),
    ]
    dec = coord._aggregate("BTC", forecasts)
    # High disagreement — multiplier should be reduced
    assert dec.max_risk_multiplier <= 0.25 or not dec.allow_trade


# ── combine_strategy_and_llm ───────────────────────────────────────────────

def test_combine_llm_disabled():
    base = _make_strategy_decision("PLACE_BUY")
    llm  = _make_decision(allow=True)
    llm.enabled = False
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "PLACE_BUY"


def test_combine_base_no_trade():
    base = _make_strategy_decision("SKIP")
    llm  = _make_decision(allow=True)
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"


def test_combine_llm_blocks():
    base = _make_strategy_decision("PLACE_BUY")
    llm  = _make_decision(allow=False)
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"


def test_combine_direction_conflict_long_vs_short():
    base = _make_strategy_decision("PLACE_BUY")
    llm  = _make_decision(action="SHORT", allow=True)
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"


def test_combine_direction_conflict_short_vs_long():
    base = _make_strategy_decision("PLACE_SELL")
    llm  = _make_decision(action="LONG", allow=True)
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"


def test_combine_size_reduced():
    base = _make_strategy_decision("PLACE_BUY", notional=100.0)
    llm  = _make_decision(action="LONG", allow=True, mult=0.5)
    result = combine_strategy_and_llm(base, llm)
    assert result.notional_usd == pytest.approx(50.0)


def test_combine_size_never_increased():
    base = _make_strategy_decision("PLACE_BUY", notional=100.0)
    llm  = _make_decision(action="LONG", allow=True, mult=1.0)
    result = combine_strategy_and_llm(base, llm)
    assert result.notional_usd <= 100.0


def test_combine_zero_multiplier_blocks():
    base = _make_strategy_decision("PLACE_BUY")
    llm  = _make_decision(action="LONG", allow=True, mult=0.0)
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"


def test_action_to_direction():
    assert _action_to_direction("PLACE_BUY")  == "LONG"
    assert _action_to_direction("PLACE_SELL") == "SHORT"
    assert _action_to_direction("PLACE_QUOTES") == "NEUTRAL"
    assert _action_to_direction("SKIP") == "NEUTRAL"
