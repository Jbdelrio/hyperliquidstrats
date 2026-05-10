"""
llm_agents/coordinator.py — Orchestrates LLM agents and combines their output.

Two architectures:
  independent_ensemble  — agents run in parallel, aggregated by median
  sequential_pipeline   — agents share context sequentially

Also provides combine_strategy_and_llm() which translates LLMDecision
into a modified StrategyDecision. This is the ONLY integration point
between the LLM layer and the execution engine.
"""
from __future__ import annotations

import logging
import statistics
from typing import TYPE_CHECKING

from llm_agents.agents import build_agents
from llm_agents.config import (
    BLOCKING_RISK_FLAGS,
    LLM_ARCHITECTURE,
    LLM_HORIZON_MINUTES,
    LLM_MAX_DISAGREEMENT,
    LLM_MIN_EDGE_PROB,
    LLM_REQUIRE_RISK_APPROVAL,
    LLM_USE_CROSS_EXCHANGE,
)
from llm_agents.providers import build_provider
from llm_agents.schemas import AgentForecast, LLMDecision, MarketSnapshot, safe_decision

if TYPE_CHECKING:
    from strategies.base_strategy import StrategyDecision

log = logging.getLogger(__name__)

_CONFIDENCE_MULTIPLIER = {"low": 0.0, "medium": 0.5, "high": 1.0}


class LLMCoordinator:
    """Main entry point for the LLM overlay."""

    def __init__(self) -> None:
        self.provider     = build_provider()
        self.agents       = build_agents(self.provider)
        self.architecture = LLM_ARCHITECTURE
        log.info("LLMCoordinator | provider=%s | architecture=%s",
                 type(self.provider).__name__, self.architecture)

    def evaluate(self, snapshot: MarketSnapshot) -> LLMDecision:
        """Run agents and return an LLMDecision. Never raises — safe fallback on error."""
        try:
            if self.architecture == "sequential_pipeline":
                return self._sequential_pipeline(snapshot)
            return self._independent_ensemble(snapshot)
        except Exception as exc:
            log.error("LLMCoordinator.evaluate error: %s", exc, exc_info=True)
            return safe_decision(snapshot.symbol, reason="llm_error",
                                 architecture=self.architecture)

    # ── Independent Ensemble ───────────────────────────────────────────────

    def _independent_ensemble(self, snap: MarketSnapshot) -> LLMDecision:
        agents_to_run = ["price_action", "microstructure", "strategy_critic", "risk_manager"]
        if LLM_USE_CROSS_EXCHANGE and snap.cross_exchange_data:
            agents_to_run.append("cross_exchange")

        forecasts: list[AgentForecast] = []
        for name in agents_to_run:
            fc = self.agents[name].run(snap)
            forecasts.append(fc)
            log.debug("[ensemble] %s → %s p_up=%.3f flags=%s",
                      name, fc.suggested_action, fc.prob_up, fc.risk_flags)

        return self._aggregate(snap.symbol, forecasts)

    # ── Sequential Pipeline ────────────────────────────────────────────────

    def _sequential_pipeline(self, snap: MarketSnapshot) -> LLMDecision:
        forecasts: list[AgentForecast] = []
        context: dict = {}

        order = ["price_action", "microstructure", "strategy_critic", "risk_manager"]
        if LLM_USE_CROSS_EXCHANGE and snap.cross_exchange_data:
            order = ["price_action", "microstructure", "cross_exchange",
                     "strategy_critic", "risk_manager"]

        for name in order:
            fc = self.agents[name].run(snap, context=context)
            forecasts.append(fc)
            context[name] = {
                "suggested_action": fc.suggested_action,
                "confidence":       fc.confidence,
                "reasoning":        fc.reasoning[:100],
                "risk_flags":       fc.risk_flags,
            }
            log.debug("[pipeline] %s → %s p_up=%.3f flags=%s",
                      name, fc.suggested_action, fc.prob_up, fc.risk_flags)

        return self._aggregate(snap.symbol, forecasts)

    # ── Aggregation logic ──────────────────────────────────────────────────

    def _aggregate(self, symbol: str,
                   forecasts: list[AgentForecast]) -> LLMDecision:
        if not forecasts:
            return safe_decision(symbol, "no_agents", self.architecture)

        # Aggregate probabilities by median
        probs_up   = [f.prob_up   for f in forecasts]
        probs_down = [f.prob_down for f in forecasts]
        med_up   = statistics.median(probs_up)
        med_down = statistics.median(probs_down)

        # Normalise
        total = med_up + med_down
        if total > 0:
            med_up   /= total
            med_down /= total
        else:
            med_up = med_down = 0.5

        # Disagreement: spread of prob_up values
        disagreement = max(probs_up) - min(probs_up)

        # Aggregate risk flags
        all_flags: list[str] = []
        for f in forecasts:
            all_flags.extend(f.risk_flags)
        unique_flags = sorted(set(all_flags))

        # Blocking flags check
        has_blocking = bool(set(unique_flags) & BLOCKING_RISK_FLAGS)

        # Risk manager veto (REDUCE_ONLY or NO_TRADE → block)
        rm_forecast = next((f for f in forecasts
                            if f.agent_name == "RiskManagerAgent"), None)
        rm_veto = (
            LLM_REQUIRE_RISK_APPROVAL
            and rm_forecast is not None
            and rm_forecast.suggested_action in ("NO_TRADE", "REDUCE_ONLY")
        )

        # CrossExchangeAgent anomaly veto
        cx_forecast = next((f for f in forecasts
                            if f.agent_name == "CrossExchangeAgent"), None)
        cx_veto = (
            cx_forecast is not None
            and cx_forecast.suggested_action == "NO_TRADE"
            and bool(set(cx_forecast.risk_flags) & {
                "price_deviation_high", "bad_execution_venue", "cross_imbalance_conflict"
            })
        )

        # Final action direction
        if med_up >= LLM_MIN_EDGE_PROB:
            final_action = "LONG"
        elif med_down >= LLM_MIN_EDGE_PROB:
            final_action = "SHORT"
        else:
            final_action = "NO_TRADE"

        # Confidence: worst of all agents weighted by agreement
        confidence_scores = [_CONFIDENCE_MULTIPLIER.get(f.confidence, 0.0)
                             for f in forecasts]
        avg_conf = statistics.mean(confidence_scores) if confidence_scores else 0.0
        if disagreement > LLM_MAX_DISAGREEMENT:
            avg_conf = min(avg_conf, 0.25)
        if avg_conf >= 0.9:
            final_conf = "high"
        elif avg_conf >= 0.4:
            final_conf = "medium"
        else:
            final_conf = "low"

        # Risk multiplier
        if has_blocking or rm_veto or cx_veto or final_action == "NO_TRADE":
            allow_trade   = False
            risk_mult     = 0.0
        elif disagreement > LLM_MAX_DISAGREEMENT:
            allow_trade   = True
            risk_mult     = min(_CONFIDENCE_MULTIPLIER.get(final_conf, 0.0), 0.25)
        else:
            allow_trade   = True
            risk_mult     = _CONFIDENCE_MULTIPLIER.get(final_conf, 0.0)

        # Build reason string
        reasons = []
        if not allow_trade:
            if has_blocking:  reasons.append(f"blocking_flags={unique_flags}")
            if rm_veto:       reasons.append("risk_manager_veto")
            if cx_veto:       reasons.append("cross_exchange_veto")
            if final_action == "NO_TRADE": reasons.append("no_edge")
        reason = "; ".join(reasons) if reasons else f"allow_trade p_up={med_up:.3f}"

        return LLMDecision(
            enabled=True,
            architecture=self.architecture,
            symbol=symbol,
            horizon_minutes=LLM_HORIZON_MINUTES,
            final_prob_up=round(med_up,   4),
            final_prob_down=round(med_down, 4),
            final_confidence=final_conf,
            final_action=final_action,
            allow_trade=allow_trade,
            max_risk_multiplier=round(risk_mult, 4),
            reason=reason,
            risk_flags=unique_flags,
            agent_forecasts=forecasts,
            raw_metadata={
                "disagreement": round(disagreement, 4),
                "rm_veto":      rm_veto,
                "cx_veto":      cx_veto,
                "n_agents":     len(forecasts),
            },
        )


# ── Signal combination ─────────────────────────────────────────────────────

def combine_strategy_and_llm(
    base_decision,        # StrategyDecision
    llm_decision: LLMDecision,
):
    """
    Merge base strategy decision with LLM decision.
    Returns a (possibly modified) StrategyDecision.

    Rules:
      - LLM disabled or base is NO_TRADE/SKIP → return base unchanged
      - LLM allow_trade=False → return SKIP
      - LLM action conflicts with base action → return SKIP
      - LLM confirms → scale notional by risk_multiplier (never above original)
      - Size NEVER increases above base notional
    """
    from strategies.base_strategy import StrategyDecision

    action = base_decision.action

    # Pass-through conditions: LLM has no say
    if not llm_decision.enabled:
        return base_decision
    if action in ("SKIP", "CANCEL_QUOTES", "CLOSE"):
        return base_decision
    # DummyProvider (no real LLM configured) → never block trades
    if "dummy_provider" in (llm_decision.risk_flags or []):
        return base_decision

    # Map strategy action to direction
    base_direction = _action_to_direction(action)

    # LLM blocks the trade
    if not llm_decision.allow_trade:
        log.info("[LLM] BLOCK %s %s — %s | flags=%s",
                 base_decision.symbol, action,
                 llm_decision.reason, llm_decision.risk_flags)
        return StrategyDecision(
            action="SKIP",
            symbol=base_decision.symbol,
            reason=f"llm_block: {llm_decision.reason}",
        )

    # Direction conflict
    llm_direction = llm_decision.final_action  # LONG / SHORT / NO_TRADE
    if (base_direction == "LONG" and llm_direction == "SHORT") or \
       (base_direction == "SHORT" and llm_direction == "LONG"):
        log.info("[LLM] CONFLICT %s base=%s llm=%s → SKIP",
                 base_decision.symbol, base_direction, llm_direction)
        return StrategyDecision(
            action="SKIP",
            symbol=base_decision.symbol,
            reason=f"llm_conflict: base={base_direction} llm={llm_direction}",
        )

    # Reduce size if multiplier < 1
    mult = llm_decision.max_risk_multiplier
    if mult <= 0.0:
        return StrategyDecision(
            action="SKIP",
            symbol=base_decision.symbol,
            reason=f"llm_zero_multiplier confidence={llm_decision.final_confidence}",
        )

    if mult < 1.0 and base_decision.notional_usd is not None:
        new_notional = base_decision.notional_usd * mult
        log.debug("[LLM] SCALE %s notional %.2f → %.2f (mult=%.2f conf=%s)",
                  base_decision.symbol, base_decision.notional_usd,
                  new_notional, mult, llm_decision.final_confidence)
        import dataclasses
        return dataclasses.replace(base_decision, notional_usd=new_notional)

    log.debug("[LLM] CONFIRM %s %s p_up=%.3f conf=%s",
              base_decision.symbol, action,
              llm_decision.final_prob_up, llm_decision.final_confidence)
    return base_decision


def _action_to_direction(action: str) -> str:
    if action in ("PLACE_BUY",):
        return "LONG"
    if action in ("PLACE_SELL",):
        return "SHORT"
    if action == "PLACE_QUOTES":
        return "NEUTRAL"  # market-making, not directional
    return "NEUTRAL"
