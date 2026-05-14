"""
llm_agents/modes.py — Three-mode LLM safety wrapper.

The engine never talks to the LLM coordinator directly. It calls
apply_llm_mode(mode, overlay, decision, snapshot, ...). The wrapper
returns an LLMResult that the engine uses to BLOCK or REDUCE a trade.

Invariants enforced here (cannot be bypassed without editing this file):

  1. LLM CAN NEVER create a trade — apply_llm_mode is called only
     after a strategy has produced a StrategyDecision; the LLM only
     decides what to do WITH the existing decision.

  2. LLM CAN NEVER increase notional — the resulting multiplier is
     hard-capped to <= 1.0 in two places: (a) inside
     LLMRiskResponse.__post_init__, (b) again with `min(...,1.0)` in
     this wrapper.

  3. LLM CAN NEVER flip a side — apply_llm_mode does not return a
     side or an action; it returns only a CONFIRM/REDUCE/BLOCK label
     plus a 0/0.5/1.0 multiplier. The engine never replaces the
     decision's action based on this.

  4. LLM CAN NEVER touch stop_loss / take_profit — those are not in
     LLMRiskResponse at all.

  5. Any exception from the underlying overlay results in PASSTHROUGH
     (i.e. the trade is not blocked AND not modified). This is the
     conservative choice for a paper bot.
"""
from __future__ import annotations

import logging
from typing import Optional, NamedTuple

from llm_agents.schemas import LLMRiskResponse, LLMSnapshot

log = logging.getLogger(__name__)


# ── Modes ────────────────────────────────────────────────────────────
MODE_OFF          = "OFF"
MODE_OBSERVER     = "OBSERVER"
MODE_RISK_OVERLAY = "RISK_OVERLAY"
VALID_MODES       = frozenset({MODE_OFF, MODE_OBSERVER, MODE_RISK_OVERLAY})


class LLMResult(NamedTuple):
    action: str       # "CONFIRM" | "REDUCE_SIZE_50" | "BLOCK" | "PASSTHROUGH"
    multiplier: float # in [0.0, 1.0] — applied to base notional
    reason: str
    confidence: float
    risk_flags: list


def _passthrough(reason: str = "passthrough") -> LLMResult:
    return LLMResult(
        action="PASSTHROUGH", multiplier=1.0,
        reason=reason, confidence=0.0, risk_flags=[],
    )


def _log_observer_result(overlay, decision, snapshot: LLMSnapshot) -> None:
    """OBSERVER mode logging — best-effort, never blocks."""
    try:
        # If the overlay knows how to evaluate this snapshot type, do it
        # and discard the result; otherwise just log the snapshot.
        if hasattr(overlay, "evaluate"):
            try:
                resp = overlay.evaluate(snapshot)
                log.info("[LLM OBSERVER] %s %s strategy=%s side=%s notional=%.2f "
                         "decision=%s reason=%s",
                         snapshot.symbol, decision.action, snapshot.strategy,
                         snapshot.side, snapshot.notional,
                         getattr(resp, "final_action", "?"),
                         getattr(resp, "reason", ""))
                return
            except Exception:
                # Fall back to snapshot-only log
                pass
        log.info("[LLM OBSERVER] %s %s strategy=%s side=%s notional=%.2f",
                 snapshot.symbol, decision.action, snapshot.strategy,
                 snapshot.side, snapshot.notional)
    except Exception as exc:
        log.debug("[LLM OBSERVER] logging error (ignored): %s", exc)


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────

def apply_llm_mode(
    mode: str,
    overlay,
    decision,
    snapshot: LLMSnapshot,
    risk_overlay_callable=None,
) -> LLMResult:
    """
    Resolve the LLM mode and produce an LLMResult.

    Parameters
    ----------
    mode : "OFF" | "OBSERVER" | "RISK_OVERLAY"
    overlay : optional object with an .evaluate() method (used by OBSERVER)
    decision : StrategyDecision (read-only here)
    snapshot : LLMSnapshot built by the engine from market data
    risk_overlay_callable : optional callable(snapshot) -> raw_dict
        Used only in RISK_OVERLAY mode. If None, falls back to
        overlay.evaluate_risk() if available, otherwise PASSTHROUGH.

    Returns
    -------
    LLMResult: a NamedTuple with (action, multiplier, reason,
    confidence, risk_flags). The engine must apply multiplier to the
    decision's notional and BLOCK the trade if multiplier == 0.
    """
    mode = (mode or MODE_OFF).upper()
    if mode not in VALID_MODES:
        return _passthrough(f"llm_invalid_mode:{mode}")

    if mode == MODE_OFF:
        return _passthrough("llm_off")

    if mode == MODE_OBSERVER:
        if overlay is not None:
            try:
                _log_observer_result(overlay, decision, snapshot)
            except Exception as exc:
                log.debug("[LLM OBSERVER] error (ignored): %s", exc)
        return _passthrough("llm_observer")

    # RISK_OVERLAY ─────────────────────────────────────────────────────
    if mode == MODE_RISK_OVERLAY:
        # Resolve the callable that returns the raw response dict.
        call = risk_overlay_callable
        if call is None and overlay is not None:
            # Prefer a dedicated method if the overlay exposes one.
            if hasattr(overlay, "evaluate_risk"):
                call = overlay.evaluate_risk

        if call is None:
            return _passthrough("llm_unavailable")

        try:
            raw = call(snapshot)
        except Exception as exc:
            log.warning("[LLM RISK_OVERLAY] error (passthrough): %s", exc)
            return _passthrough(f"llm_error:{exc}")

        try:
            resp = LLMRiskResponse.model_validate(raw)
        except Exception as exc:
            log.warning("[LLM RISK_OVERLAY] schema error (passthrough): %s", exc)
            return _passthrough(f"llm_parse_error:{exc}")

        # Hard safety: clip multiplier once more in case the schema layer
        # is ever bypassed by a future refactor. (Defense in depth.)
        multiplier = min(1.0, max(0.0, float(resp.max_risk_multiplier)))
        if resp.decision == "BLOCK":
            multiplier = 0.0
        elif resp.decision == "REDUCE_SIZE_50":
            # REDUCE_SIZE_50 is the verb; the multiplier is the noun.
            # Take the more conservative of (multiplier, 0.5).
            multiplier = min(multiplier, 0.5)
        # CONFIRM keeps multiplier as-is (still capped to 1.0)

        return LLMResult(
            action=resp.decision,
            multiplier=multiplier,
            reason=resp.reason,
            confidence=resp.confidence,
            risk_flags=list(resp.risk_flags),
        )

    # Unreachable
    return _passthrough("llm_fallthrough")
