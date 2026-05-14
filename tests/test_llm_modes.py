"""tests/test_llm_modes.py — Three-mode LLM safety wrapper tests."""
from __future__ import annotations

from llm_agents.modes import (
    LLMResult, MODE_OFF, MODE_OBSERVER, MODE_RISK_OVERLAY, apply_llm_mode,
)
from llm_agents.schemas import LLMRiskResponse, LLMSnapshot
from strategies.base_strategy import StrategyDecision


def _snap(notional=20.0):
    return LLMSnapshot(
        symbol="BTC", strategy="MomentumLS", side="BUY",
        entry=100.0, stop=99.0, take_profit=102.0,
        notional=notional,
    )


def _dec(action="PLACE_BUY", notional=20.0):
    return StrategyDecision(
        action=action, symbol="BTC",
        notional_usd=notional,
        stop_loss=99.0, take_profit=102.0,
    )


def test_off_mode_always_passthrough():
    res = apply_llm_mode(MODE_OFF, overlay=None,
                         decision=_dec(), snapshot=_snap())
    assert res.action == "PASSTHROUGH"
    assert res.multiplier == 1.0


def test_observer_mode_always_passthrough_even_if_llm_blocks():
    """OBSERVER mode never modifies the trade — even if overlay would block."""
    class _Overlay:
        def evaluate(self, snap):
            # Pretend the LLM would block
            class _R:
                final_action = "NO_TRADE"
                reason = "would_block"
            return _R()
    res = apply_llm_mode(MODE_OBSERVER, overlay=_Overlay(),
                         decision=_dec(), snapshot=_snap())
    assert res.action == "PASSTHROUGH"
    assert res.multiplier == 1.0


def test_risk_overlay_block_returns_zero_multiplier():
    def fake(snap):
        return {"decision": "BLOCK", "confidence": 0.9, "reason": "spread_too_wide"}
    res = apply_llm_mode(MODE_RISK_OVERLAY, overlay=None,
                         decision=_dec(), snapshot=_snap(),
                         risk_overlay_callable=fake)
    assert res.action == "BLOCK"
    assert res.multiplier == 0.0


def test_risk_overlay_reduce_returns_half_multiplier():
    def fake(snap):
        return {
            "decision": "REDUCE_SIZE_50", "confidence": 0.6,
            "reason": "elevated_vol", "max_risk_multiplier": 1.0,
        }
    res = apply_llm_mode(MODE_RISK_OVERLAY, overlay=None,
                         decision=_dec(), snapshot=_snap(),
                         risk_overlay_callable=fake)
    assert res.action == "REDUCE_SIZE_50"
    assert res.multiplier == 0.5


def test_risk_overlay_confirm_returns_full_multiplier():
    def fake(snap):
        return {
            "decision": "CONFIRM", "confidence": 0.8,
            "reason": "clean", "max_risk_multiplier": 1.0,
        }
    res = apply_llm_mode(MODE_RISK_OVERLAY, overlay=None,
                         decision=_dec(), snapshot=_snap(),
                         risk_overlay_callable=fake)
    assert res.action == "CONFIRM"
    assert res.multiplier == 1.0


def test_llm_cannot_increase_multiplier_above_one():
    """LLM trying to scale UP must be clipped to 1.0."""
    def fake(snap):
        # Try to inject multiplier = 5.0
        return {
            "decision": "CONFIRM", "confidence": 1.0,
            "reason": "more_size", "max_risk_multiplier": 5.0,
        }
    res = apply_llm_mode(MODE_RISK_OVERLAY, overlay=None,
                         decision=_dec(), snapshot=_snap(),
                         risk_overlay_callable=fake)
    assert res.multiplier <= 1.0
    # Also verify the schema layer clips
    resp = LLMRiskResponse.model_validate({"decision": "CONFIRM",
                                             "max_risk_multiplier": 5.0})
    assert resp.max_risk_multiplier == 1.0


def test_llm_error_returns_passthrough():
    """If the underlying callable raises, we must PASSTHROUGH (not block)."""
    def boom(snap):
        raise RuntimeError("network down")
    res = apply_llm_mode(MODE_RISK_OVERLAY, overlay=None,
                         decision=_dec(), snapshot=_snap(),
                         risk_overlay_callable=boom)
    assert res.action == "PASSTHROUGH"
    assert res.multiplier == 1.0
    assert "llm_error" in res.reason


def test_invalid_json_returns_passthrough():
    """A malformed dict (e.g. wrong type) must result in PASSTHROUGH."""
    def garbage(snap):
        return "not a dict"
    res = apply_llm_mode(MODE_RISK_OVERLAY, overlay=None,
                         decision=_dec(), snapshot=_snap(),
                         risk_overlay_callable=garbage)
    assert res.action == "PASSTHROUGH"


def test_invalid_decision_is_treated_as_block():
    """Schema layer fallback: invalid decision string → BLOCK in schema."""
    resp = LLMRiskResponse.model_validate({"decision": "BUY_MORE"})
    assert resp.decision == "BLOCK"
    assert "llm_invalid_decision" in resp.risk_flags


def test_risk_overlay_unavailable_returns_passthrough():
    """No callable + no overlay → PASSTHROUGH."""
    res = apply_llm_mode(MODE_RISK_OVERLAY, overlay=None,
                         decision=_dec(), snapshot=_snap(),
                         risk_overlay_callable=None)
    assert res.action == "PASSTHROUGH"
    assert res.reason == "llm_unavailable"


def test_invalid_mode_is_passthrough():
    res = apply_llm_mode("CHAOS_MODE", overlay=None,
                         decision=_dec(), snapshot=_snap())
    assert res.action == "PASSTHROUGH"


def test_llm_response_negative_multiplier_clamped_to_zero():
    resp = LLMRiskResponse.model_validate({"decision": "CONFIRM",
                                             "max_risk_multiplier": -5.0})
    assert resp.max_risk_multiplier == 0.0
