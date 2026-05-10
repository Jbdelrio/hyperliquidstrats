"""
tests/test_llm_schemas.py — Unit tests for llm_agents/schemas.py
"""
import pytest
from llm_agents.schemas import (
    AgentForecast,
    LLMDecision,
    _normalize_probs,
    parse_agent_forecast,
    safe_decision,
)


def test_normalize_probs_basic():
    up, down = _normalize_probs(0.6, 0.4)
    assert abs(up + down - 1.0) < 1e-6
    assert up > down


def test_normalize_probs_skewed():
    up, down = _normalize_probs(0.8, 0.8)
    assert abs(up - 0.5) < 1e-6
    assert abs(down - 0.5) < 1e-6


def test_normalize_probs_zero():
    up, down = _normalize_probs(0.0, 0.0)
    assert up == 0.5
    assert down == 0.5


def test_normalize_probs_clamp():
    up, down = _normalize_probs(1.5, -0.2)
    assert 0.0 <= up <= 1.0
    assert 0.0 <= down <= 1.0


def test_agent_forecast_post_init():
    f = AgentForecast(
        agent_name="test", prob_up=0.7, prob_down=0.3,
        confidence="medium", horizon_minutes=60, reasoning="ok",
    )
    assert abs(f.prob_up + f.prob_down - 1.0) < 1e-6
    assert f.suggested_action == "NO_TRADE"


def test_agent_forecast_invalid_confidence():
    f = AgentForecast(
        agent_name="test", prob_up=0.5, prob_down=0.5,
        confidence="extreme", horizon_minutes=60, reasoning="",
    )
    assert f.confidence == "low"


def test_agent_forecast_invalid_action():
    f = AgentForecast(
        agent_name="test", prob_up=0.5, prob_down=0.5,
        confidence="high", horizon_minutes=60, reasoning="",
        suggested_action="BUY_NOW",
    )
    assert f.suggested_action == "NO_TRADE"


def test_llm_decision_risk_mult_clamp():
    d = LLMDecision(
        enabled=True, architecture="test", symbol="BTC",
        horizon_minutes=60, final_prob_up=0.6, final_prob_down=0.4,
        final_confidence="high", final_action="LONG",
        allow_trade=True, max_risk_multiplier=1.5, reason="ok",
    )
    assert d.max_risk_multiplier == 1.0


def test_safe_decision():
    d = safe_decision("ETH", reason="test_error")
    assert d.allow_trade is False
    assert d.final_action == "NO_TRADE"
    assert d.final_confidence == "low"
    assert "test_error" in d.risk_flags


def test_parse_agent_forecast_valid():
    raw = {
        "agent_name": "PriceActionAgent",
        "prob_up": 0.6,
        "prob_down": 0.4,
        "confidence": "medium",
        "horizon_minutes": 60,
        "reasoning": "trend up",
        "risk_flags": [],
        "suggested_action": "LONG",
        "expected_edge_bps": 12.5,
    }
    f = parse_agent_forecast(raw, "PriceActionAgent")
    assert f.agent_name == "PriceActionAgent"
    assert f.suggested_action == "LONG"
    assert f.expected_edge_bps == 12.5


def test_parse_agent_forecast_invalid_json():
    # Provide a value that causes a TypeError on float() conversion
    f = parse_agent_forecast({"prob_up": "not_a_number"}, "TestAgent")
    assert f.suggested_action == "NO_TRADE"
    assert "llm_parse_error" in f.risk_flags


def test_parse_agent_forecast_missing_fields():
    f = parse_agent_forecast({}, "TestAgent")
    assert f.prob_up == 0.5
    assert f.prob_down == 0.5
    assert f.confidence == "low"
