"""
llm_agents/logger.py — Structured logging for LLM decisions.

Wraps calibration.PredictionLogger and adds per-run JSON logging.
All LLM decisions (allow or block) are logged; nothing is discarded.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Optional

from llm_agents.calibration import PredictionLogger
from llm_agents.config import LLM_LOG_PREDICTIONS
from llm_agents.schemas import LLMDecision

log = logging.getLogger(__name__)

_LAST_DECISIONS: dict[str, LLMDecision] = {}  # symbol → latest decision (in-memory)


class LLMLogger:
    def __init__(self, csv_path: Path = Path("data/llm_predictions.csv")) -> None:
        self._pred_logger = PredictionLogger(csv_path) if LLM_LOG_PREDICTIONS else None

    def log(self, decision: LLMDecision, strategy_context: str = "") -> None:
        _LAST_DECISIONS[decision.symbol] = decision
        if self._pred_logger and LLM_LOG_PREDICTIONS:
            self._pred_logger.log_prediction(decision, strategy_context)
        log.info(
            "[LLM] %s | arch=%s | action=%s | allow=%s | p_up=%.3f | conf=%s"
            " | mult=%.2f | flags=%s | reason=%s",
            decision.symbol, decision.architecture,
            decision.final_action, decision.allow_trade,
            decision.final_prob_up, decision.final_confidence,
            decision.max_risk_multiplier, decision.risk_flags,
            decision.reason[:80],
        )

    def get_last_decision(self, symbol: str) -> Optional[LLMDecision]:
        return _LAST_DECISIONS.get(symbol)

    def get_all_last_decisions(self) -> dict[str, LLMDecision]:
        return dict(_LAST_DECISIONS)

    def get_rolling_brier(self, window: int = 50) -> Optional[float]:
        if self._pred_logger:
            return self._pred_logger.get_rolling_brier(window)
        return None

    def load_predictions(self) -> list[dict]:
        if self._pred_logger:
            return self._pred_logger.load_predictions()
        return []


_default_logger: Optional[LLMLogger] = None


def get_llm_logger() -> LLMLogger:
    global _default_logger
    if _default_logger is None:
        _default_logger = LLMLogger()
    return _default_logger


def last_decisions() -> dict:
    """Return serialisable snapshot of last LLM decision per symbol."""
    out = {}
    for sym, dec in _LAST_DECISIONS.items():
        out[sym] = {
            "final_action":      dec.final_action,
            "allow_trade":       dec.allow_trade,
            "final_prob_up":     dec.final_prob_up,
            "final_confidence":  dec.final_confidence,
            "max_risk_mult":     dec.max_risk_multiplier,
            "risk_flags":        list(dec.risk_flags),
            "reason":            dec.reason[:120],
            "architecture":      dec.architecture,
        }
    return out
