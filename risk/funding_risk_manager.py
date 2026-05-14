"""
funding_risk_manager.py — Hard gates for funding arbitrage trades.

Every prospective funding trade must pass `check(opportunity, state)` and
get a `(True, "")` answer before being routed (in paper) or even
considered. Gates are pure functions — no side effects, no state
mutation. State (current open exposure, etc.) is passed in.

In the current code base only `paper` mode is supported. The risk
manager still enforces every gate so the *moment* live is introduced
the bar is already high.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FundingRiskState:
    open_notional_total: float = 0.0
    open_notional_by_exchange: dict = None
    open_notional_by_symbol: dict = None

    def __post_init__(self):
        if self.open_notional_by_exchange is None:
            self.open_notional_by_exchange = {}
        if self.open_notional_by_symbol is None:
            self.open_notional_by_symbol = {}


@dataclass
class FundingRiskLimits:
    max_notional_per_trade: float = 25.0
    max_total_funding_notional: float = 100.0
    min_liquidation_buffer: float = 0.5     # in fraction of margin
    max_basis_bps: float = 30.0
    max_funding_volatility: float = 0.5     # std/mean ratio of recent funding
    max_hedge_error_usd: float = 1.0
    max_exchange_exposure: float = 100.0
    max_symbol_exposure: float = 50.0
    min_expected_net_carry_usd: float = 0.05
    min_funding_spread_bps: float = 3.0
    min_liquidity_score: float = 0.70
    max_risk_score: float = 0.40
    allow_live: bool = False


class FundingRiskManager:

    def __init__(self, limits: Optional[FundingRiskLimits] = None):
        self.limits = limits or FundingRiskLimits()

    def check(self, opp, state: Optional[FundingRiskState] = None,
              live_requested: bool = False) -> tuple[bool, str]:
        """Return (ok, reason). `opp` is a FundingOpportunity-like object."""
        L = self.limits
        if state is None:
            state = FundingRiskState()

        if live_requested and not L.allow_live:
            return False, "live_blocked"

        notional = float(getattr(opp, "notional_usd", 0.0))
        if notional <= 0:
            return False, "notional_zero_or_negative"
        if notional > L.max_notional_per_trade:
            return False, "max_notional_per_trade_exceeded"

        # Carry edge gates
        net_usd = float(getattr(opp, "expected_net_usd", 0.0))
        if net_usd < L.min_expected_net_carry_usd:
            return False, "expected_net_carry_too_low"

        # Quality / risk gates
        liq = float(getattr(opp, "liquidity_score", 0.0))
        if liq < L.min_liquidity_score:
            return False, "liquidity_too_low"
        risk = float(getattr(opp, "risk_score", 0.0))
        if risk > L.max_risk_score:
            return False, "risk_score_too_high"

        # Basis gate (cross-exchange only)
        basis = abs(float(getattr(opp, "basis_bps", 0.0)))
        if basis > L.max_basis_bps:
            return False, "basis_too_wide"

        # Mode-specific gates
        mode = getattr(opp, "mode", "single")
        if mode == "cross_exchange":
            le = getattr(opp, "long_exchange", None)
            se = getattr(opp, "short_exchange", None)
            if not le or not se:
                return False, "cross_exchange_leg_missing"
            spread_bps = abs(float(getattr(opp, "expected_funding_bps", 0.0)))
            if spread_bps < L.min_funding_spread_bps:
                return False, "funding_spread_too_small"

        # Exposure gates
        total = state.open_notional_total + notional
        if total > L.max_total_funding_notional:
            return False, "max_total_funding_notional_exceeded"
        for exch in (getattr(opp, "long_exchange", None),
                     getattr(opp, "short_exchange", None)):
            if not exch:
                continue
            cur = state.open_notional_by_exchange.get(exch, 0.0) + notional
            if cur > L.max_exchange_exposure:
                return False, f"max_exchange_exposure:{exch}"
        sym = getattr(opp, "symbol", None)
        if sym:
            cur_s = state.open_notional_by_symbol.get(sym, 0.0) + notional
            if cur_s > L.max_symbol_exposure:
                return False, "max_symbol_exposure_exceeded"

        return True, ""
