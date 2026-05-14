"""
funding_data.py — Common dataclasses for funding rate snapshots + opportunities.

A `FundingSnapshot` is the minimal cross-exchange representation of a
perp funding state. Exchange adapters (`data/exchange_adapters/*`) are
expected to produce these.

A `FundingOpportunity` is the output of the scanner — a candidate single-
or cross-exchange trade with all metadata needed to log, gate, and
decide.

NO live execution is performed here. This module is data-only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FundingSnapshot:
    exchange: str
    symbol: str
    timestamp: float
    funding_rate: float                       # hourly rate (normalized)
    next_funding_time: Optional[float] = None
    mark_price: Optional[float] = None
    oracle_price: Optional[float] = None
    index_price: Optional[float] = None
    open_interest: Optional[float] = None
    raw: dict = field(default_factory=dict)

    @property
    def funding_rate_bps(self) -> float:
        """Hourly funding rate in basis points."""
        try:
            return float(self.funding_rate) * 10_000.0
        except (TypeError, ValueError):
            return float("nan")


@dataclass
class FundingOpportunity:
    """A scanner-produced funding opportunity (paper-only by default)."""
    symbol: str
    long_exchange: Optional[str]
    short_exchange: Optional[str]
    direction: str          # "long" / "short" / "neutral" / "n/a"
    notional_usd: float
    expected_funding_bps: float     # per hour
    expected_net_bps: float         # over horizon, net of costs
    expected_net_usd: float
    estimated_cost_bps: float
    basis_bps: float = 0.0
    liquidity_score: float = 0.0
    stability_score: float = 0.0
    risk_score: float = 0.0
    reason: str = ""
    mode: str = "single"            # "single" / "cross_exchange"
    horizon_hours: int = 1

    def as_log_row(self) -> dict:
        return {
            "symbol": self.symbol,
            "long_exchange": self.long_exchange or "",
            "short_exchange": self.short_exchange or "",
            "direction": self.direction,
            "mode": self.mode,
            "notional_usd": self.notional_usd,
            "expected_funding_bps": self.expected_funding_bps,
            "expected_net_bps": self.expected_net_bps,
            "expected_net_usd": self.expected_net_usd,
            "estimated_cost_bps": self.estimated_cost_bps,
            "basis_bps": self.basis_bps,
            "liquidity_score": self.liquidity_score,
            "stability_score": self.stability_score,
            "risk_score": self.risk_score,
            "horizon_hours": self.horizon_hours,
            "reason": self.reason,
        }
