"""
tests/test_llm_signal_combination.py — Integration-level signal combination tests.
"""
import os
import pytest

os.environ.setdefault("LLM_ENABLED", "false")

from strategies.base_strategy import StrategyDecision
from llm_agents.schemas import LLMDecision
from llm_agents.coordinator import combine_strategy_and_llm


def _sd(action="PLACE_BUY", notional=200.0, symbol="ETH"):
    return StrategyDecision(action=action, symbol=symbol, notional_usd=notional)


def _ld(action="LONG", allow=True, mult=1.0, conf="high", flags=None):
    return LLMDecision(
        enabled=True, architecture="test", symbol="ETH",
        horizon_minutes=60, final_prob_up=0.65, final_prob_down=0.35,
        final_confidence=conf, final_action=action,
        allow_trade=allow, max_risk_multiplier=mult,
        reason="test", risk_flags=flags or [],
    )


def test_llm_disabled_passthrough():
    base = _sd()
    llm  = _ld()
    llm.enabled = False
    assert combine_strategy_and_llm(base, llm).action == "PLACE_BUY"


def test_cancel_quotes_always_passthrough():
    base = _sd("CANCEL_QUOTES")
    llm  = _ld(allow=False)
    assert combine_strategy_and_llm(base, llm).action == "CANCEL_QUOTES"


def test_close_always_passthrough():
    base = _sd("CLOSE")
    llm  = _ld(allow=False)
    assert combine_strategy_and_llm(base, llm).action == "CLOSE"


def test_llm_block_with_high_spread():
    base = _sd()
    llm  = _ld(allow=False, flags=["high_spread"])
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"


def test_sell_with_short_confirmation():
    base = _sd("PLACE_SELL")
    llm  = _ld(action="SHORT", allow=True, mult=1.0)
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "PLACE_SELL"
    assert result.notional_usd == pytest.approx(200.0)


def test_market_making_quotes_allowed_with_neutral_llm():
    base = _sd("PLACE_QUOTES")
    llm  = _ld(action="LONG", allow=True, mult=0.8)
    result = combine_strategy_and_llm(base, llm)
    # PLACE_QUOTES is NEUTRAL direction — no conflict even with LONG signal
    assert result.action == "PLACE_QUOTES"
    assert result.notional_usd == pytest.approx(160.0)


def test_low_confidence_zero_multiplier_blocks():
    base = _sd()
    llm  = _ld(action="LONG", allow=True, mult=0.0, conf="low")
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"


def test_size_reduced_correctly():
    base = _sd(notional=500.0)
    llm  = _ld(action="LONG", allow=True, mult=0.5)
    result = combine_strategy_and_llm(base, llm)
    assert result.notional_usd == pytest.approx(250.0)


def test_size_never_exceeds_base():
    base = _sd(notional=100.0)
    llm  = _ld(action="LONG", allow=True, mult=1.0)
    result = combine_strategy_and_llm(base, llm)
    assert result.notional_usd <= 100.0


def test_no_trade_base_signal_stays_no_trade():
    base = StrategyDecision(action="SKIP", symbol="ETH")
    llm  = _ld(action="LONG", allow=True, mult=1.0)
    result = combine_strategy_and_llm(base, llm)
    assert result.action == "SKIP"
