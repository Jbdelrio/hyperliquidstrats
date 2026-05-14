"""
llm_agents/schemas.py — Dataclass schemas for LLM overlay.
All validation is explicit; no external dependencies required.

Phase-6 adds:
  - LLMRiskResponse: strict response shape from a RISK_OVERLAY LLM call.
    Hard-caps `max_risk_multiplier` to <= 1.0 and validates the decision
    enum so the LLM can never increase size or create a trade.
  - LLMSnapshot: lean, typed market snapshot used by RISK_OVERLAY.
    Kept separate from MarketSnapshot to avoid breaking existing logger
    / coordinator code paths.
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


# ─────────────────────────────────────────────────────────────────────
# Phase-6 RISK_OVERLAY schemas
# ─────────────────────────────────────────────────────────────────────


_VALID_RISK_DECISIONS = frozenset({"CONFIRM", "REDUCE_SIZE_50", "BLOCK"})


@dataclass
class LLMRiskResponse:
    """
    Strict response shape from a RISK_OVERLAY LLM call.

    Invariants (enforced in __post_init__):
      - decision ∈ {CONFIRM, REDUCE_SIZE_50, BLOCK}
      - confidence ∈ [0, 1]
      - max_risk_multiplier ∈ [0, 1] (CAN NEVER exceed 1.0)
      - risk_flags is a list[str]

    The LLM CAN return decisions that REDUCE risk. It CAN NEVER increase
    risk above the strategy's own choice — guaranteed by clipping
    `max_risk_multiplier` to <= 1.0 and by the action enum.
    """
    decision: str
    confidence: float = 0.0
    risk_flags: list = field(default_factory=list)
    reason: str = ""
    max_risk_multiplier: float = 1.0

    def __post_init__(self) -> None:
        if self.decision not in _VALID_RISK_DECISIONS:
            self.decision = "BLOCK"
            self.risk_flags = list(self.risk_flags) + ["llm_invalid_decision"]
        # Confidence ∈ [0,1]
        try:
            self.confidence = max(0.0, min(1.0, float(self.confidence)))
        except (TypeError, ValueError):
            self.confidence = 0.0
        # CRITICAL: multiplier can NEVER exceed 1.0
        try:
            self.max_risk_multiplier = max(0.0, min(1.0, float(self.max_risk_multiplier)))
        except (TypeError, ValueError):
            self.max_risk_multiplier = 0.0
        # risk_flags must be a list of strings
        try:
            self.risk_flags = [str(f) for f in (self.risk_flags or [])]
        except (TypeError, ValueError):
            self.risk_flags = []

    @classmethod
    def model_validate(cls, raw) -> "LLMRiskResponse":
        """Pydantic-style entry point. Accepts dict or LLMRiskResponse."""
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            raise ValueError(f"LLMRiskResponse expects dict, got {type(raw)}")
        return cls(
            decision=str(raw.get("decision", "BLOCK")),
            confidence=float(raw.get("confidence", 0.0) or 0.0),
            risk_flags=list(raw.get("risk_flags", []) or []),
            reason=str(raw.get("reason", "") or ""),
            max_risk_multiplier=float(raw.get("max_risk_multiplier", 1.0) or 1.0),
        )


@dataclass
class LLMSnapshot:
    """
    Lean market snapshot consumed by RISK_OVERLAY. Includes only the
    fields the risk LLM needs — strategy intent, market regime,
    portfolio context.
    """
    symbol: str
    strategy: str
    side: str
    entry: float
    stop: float
    take_profit: float
    notional: float
    spread_bps: float = 0.0
    expected_edge_bps: float = 0.0
    estimated_cost_bps: float = 0.0
    reward_risk_ratio: float = 0.0

    btc_1m_return: float = 0.0
    btc_5m_return: float = 0.0
    btc_regime: str = "unknown"
    orderbook_imbalance: float = 0.0
    funding_rate: float = 0.0

    open_positions_count: int = 0
    daily_pnl: float = 0.0
    recent_loss_streak: int = 0
    signal_reason: str = ""
