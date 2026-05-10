"""
tests/test_cross_exchange_agent.py — Tests for CrossExchangeAgent.
"""
import os
import pytest

os.environ.setdefault("LLM_ENABLED", "false")

from llm_agents.schemas import MarketSnapshot
from llm_agents.agents import CrossExchangeAgent
from llm_agents.providers import DummyLLMProvider


_agent = CrossExchangeAgent(DummyLLMProvider())


def _snap(cross_data=None):
    return MarketSnapshot(
        symbol="BTC", timestamp="2026-01-01T00:00:00Z",
        cross_exchange_data=cross_data,
        available_exchanges=["hyperliquid"] + (list(cross_data.keys()) if cross_data else []),
    )


def test_no_cross_data_returns_no_trade():
    forecast = _agent.run(_snap(cross_data=None))
    assert forecast.suggested_action == "NO_TRADE"
    assert "no_cross_exchange_data" in forecast.risk_flags


def test_with_cross_data_runs():
    cross = {
        "binance": {"mid": 50000, "spread_bps": 0.8, "funding_rate": 0.0001,
                    "orderbook_imbalance": 0.05},
        "bitget":  {"mid": 49998, "spread_bps": 1.5, "funding_rate": 0.0002,
                    "orderbook_imbalance": -0.02},
    }
    forecast = _agent.run(_snap(cross_data=cross))
    # DummyProvider returns NO_TRADE (neutral), should not crash
    assert forecast.agent_name == "CrossExchangeAgent"
    assert 0.0 <= forecast.prob_up <= 1.0
    assert 0.0 <= forecast.prob_down <= 1.0


def test_llm_cannot_call_place_order():
    """Verify the agent has no access to any exchange execution method."""
    import inspect
    source = inspect.getsource(CrossExchangeAgent)
    assert "place_order" not in source
    assert "executor" not in source
    assert "high_freq" not in source


def test_size_never_increased_by_cross_exchange():
    """Cross-exchange agent can only produce prob/action — no size field."""
    cross = {"binance": {"mid": 50010, "spread_bps": 0.5, "funding_rate": 0.00005,
                          "orderbook_imbalance": 0.15}}
    forecast = _agent.run(_snap(cross_data=cross))
    # AgentForecast has no size field — size can only be modified by coordinator
    assert not hasattr(forecast, "size")
    assert not hasattr(forecast, "notional_usd")
