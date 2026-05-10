"""
llm_agents/schemas.py — Dataclass schemas for LLM overlay.
All validation is explicit; no external dependencies required.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MarketSnapshot:
    symbol: str
    timestamp: str
    exchange: str = "hyperliquid"
    timeframe: str = "1m"

    mid_price: Optional[float] = None
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    spread_bps: Optional[float] = None

    ohlcv_tail: list = field(default_factory=list)

    funding_rate: Optional[float] = None
    open_interest: Optional[float] = None
    volume_24h: Optional[float] = None

    volatility_short: Optional[float] = None
    volatility_long: Optional[float] = None
    orderbook_imbalance: Optional[float] = None

    strategy_signals: dict = field(default_factory=dict)
    current_position: Optional[dict] = None
    account_risk: Optional[dict] = None

    available_exchanges: list = field(default_factory=list)
    cross_exchange_data: Optional[dict] = None


@dataclass
class CrossExchangeSnapshot:
    symbol: str
    timestamp: str
    exchanges: dict = field(default_factory=dict)


@dataclass
class AgentForecast:
    agent_name: str
    prob_up: float
    prob_down: float
    confidence: str           # low / medium / high
    horizon_minutes: int
    reasoning: str
    risk_flags: list = field(default_factory=list)
    suggested_action: str = "NO_TRADE"  # LONG / SHORT / NO_TRADE / REDUCE_ONLY
    expected_edge_bps: Optional[float] = None

    def __post_init__(self) -> None:
        self.prob_up, self.prob_down = _normalize_probs(self.prob_up, self.prob_down)
        self.confidence = self.confidence if self.confidence in ("low", "medium", "high") else "low"
        self.suggested_action = (
            self.suggested_action
            if self.suggested_action in ("LONG", "SHORT", "NO_TRADE", "REDUCE_ONLY")
            else "NO_TRADE"
        )


@dataclass
class LLMDecision:
    enabled: bool
    architecture: str
    symbol: str
    horizon_minutes: int
    final_prob_up: float
    final_prob_down: float
    final_confidence: str
    final_action: str
    allow_trade: bool
    max_risk_multiplier: float
    reason: str
    risk_flags: list = field(default_factory=list)
    agent_forecasts: list = field(default_factory=list)
    raw_metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.final_prob_up, self.final_prob_down = _normalize_probs(
            self.final_prob_up, self.final_prob_down
        )
        self.max_risk_multiplier = max(0.0, min(1.0, self.max_risk_multiplier))


def _normalize_probs(p_up: float, p_down: float) -> tuple[float, float]:
    """Ensure probs are in [0,1] and sum to ~1."""
    p_up   = max(0.0, min(1.0, float(p_up)))
    p_down = max(0.0, min(1.0, float(p_down)))
    total  = p_up + p_down
    if total > 0:
        p_up   /= total
        p_down /= total
    else:
        p_up = p_down = 0.5
    return round(p_up, 6), round(p_down, 6)


def safe_decision(symbol: str, reason: str = "llm_error",
                  architecture: str = "none") -> LLMDecision:
    """Return a safe NO_TRADE decision used whenever the LLM fails."""
    return LLMDecision(
        enabled=True,
        architecture=architecture,
        symbol=symbol,
        horizon_minutes=60,
        final_prob_up=0.5,
        final_prob_down=0.5,
        final_confidence="low",
        final_action="NO_TRADE",
        allow_trade=False,
        max_risk_multiplier=0.0,
        reason=reason,
        risk_flags=[reason],
        agent_forecasts=[],
        raw_metadata={},
    )


def parse_agent_forecast(raw: dict, agent_name: str) -> AgentForecast:
    """Parse LLM JSON response into AgentForecast, with safe fallback."""
    try:
        return AgentForecast(
            agent_name=raw.get("agent_name", agent_name),
            prob_up=float(raw.get("prob_up", 0.5)),
            prob_down=float(raw.get("prob_down", 0.5)),
            confidence=str(raw.get("confidence", "low")),
            horizon_minutes=int(raw.get("horizon_minutes", 60)),
            reasoning=str(raw.get("reasoning", ""))[:500],
            risk_flags=list(raw.get("risk_flags", [])),
            suggested_action=str(raw.get("suggested_action", "NO_TRADE")),
            expected_edge_bps=(
                float(raw["expected_edge_bps"])
                if raw.get("expected_edge_bps") is not None
                else None
            ),
        )
    except Exception:
        return AgentForecast(
            agent_name=agent_name,
            prob_up=0.5,
            prob_down=0.5,
            confidence="low",
            horizon_minutes=60,
            reasoning="parse_error",
            risk_flags=["llm_parse_error"],
            suggested_action="NO_TRADE",
        )
