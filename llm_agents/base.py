"""
llm_agents/base.py — Top-level overlay object injected into the engine.

LLMOverlay wraps the coordinator + logger and provides the evaluate() method
that the engine calls. It NEVER imports from execution/ or sends orders.
"""
from __future__ import annotations

import logging
from typing import Optional

from llm_agents.coordinator import LLMCoordinator, combine_strategy_and_llm
from llm_agents.logger import LLMLogger
from llm_agents.schemas import LLMDecision, MarketSnapshot

log = logging.getLogger(__name__)


class LLMOverlay:
    """Injected into EngineV9. Call evaluate() before executing a strategy decision."""

    def __init__(self) -> None:
        self.coordinator = LLMCoordinator()
        self.logger      = LLMLogger()
        self.architecture = self.coordinator.architecture

    def evaluate(self, snapshot: MarketSnapshot,
                 strategy_context: str = "") -> LLMDecision:
        """Run agents, log result, return LLMDecision. Never raises."""
        decision = self.coordinator.evaluate(snapshot)
        self.logger.log(decision, strategy_context)
        return decision

    def modify_decision(self, base_decision, llm_decision: LLMDecision):
        """Apply LLMDecision to a StrategyDecision. Never raises."""
        try:
            return combine_strategy_and_llm(base_decision, llm_decision)
        except Exception as exc:
            log.warning("LLMOverlay.modify_decision error: %s", exc)
            return base_decision

    def get_last_decision(self, symbol: str) -> Optional[LLMDecision]:
        return self.logger.get_last_decision(symbol)

    def get_all_last_decisions(self) -> dict:
        return self.logger.get_all_last_decisions()

    def get_rolling_brier(self, window: int = 50) -> Optional[float]:
        return self.logger.get_rolling_brier(window)
