"""
funding_opportunity_scanner.py — Build FundingOpportunity rows from
Hyperliquid + (optionally) Aster funding snapshots.

Pure data layer : no order routing, no live calls beyond the adapters'
cached REST fetches. The scanner is used both by `scripts/scan_funding_opportunities.py`
and by `strategies/funding_arbitrage_enhanced.py` (paper-only).
"""
from __future__ import annotations

import math
from typing import Optional

from data.funding_data import FundingOpportunity, FundingSnapshot
from data.exchange_adapters.hyperliquid_funding import HyperliquidFundingAdapter
from data.exchange_adapters.aster_funding import AsterFundingAdapter


# Default decision codes ----------------------------------------------------
DEC_NO_TRADE_LOW_EDGE         = "NO_TRADE_LOW_EDGE"
DEC_NO_TRADE_COST_TOO_HIGH    = "NO_TRADE_COST_TOO_HIGH"
DEC_NO_TRADE_BASIS_RISK       = "NO_TRADE_BASIS_RISK"
DEC_NO_TRADE_LIQUIDITY        = "NO_TRADE_LIQUIDITY"
DEC_PAPER_ONLY_SINGLE         = "PAPER_ONLY_SINGLE_EXCHANGE"
DEC_PAPER_CROSS_CANDIDATE     = "PAPER_CROSS_EXCHANGE_CANDIDATE"
DEC_EXECUTION_NOT_AVAILABLE   = "EXECUTION_NOT_AVAILABLE"


class FundingOpportunityScanner:

    def __init__(self,
                 hl_adapter: Optional[HyperliquidFundingAdapter] = None,
                 aster_adapter: Optional[AsterFundingAdapter] = None,
                 config: Optional[dict] = None):
        self.hl = hl_adapter or HyperliquidFundingAdapter()
        self.aster = aster_adapter or AsterFundingAdapter()
        cfg = config or {}
        self.notional_usd = float(cfg.get("notional_usd", 25.0))
        self.cost_bps_per_leg = float(cfg.get("cost_bps_per_leg", 4.0))
        self.min_funding_spread_bps = float(cfg.get("min_funding_spread_bps", 3.0))
        self.min_net_carry_bps = float(cfg.get("min_net_carry_bps", 2.0))
        self.min_net_carry_usd = float(cfg.get("min_net_carry_usd", 0.05))
        self.max_basis_bps = float(cfg.get("max_basis_bps", 30.0))
        self.min_liquidity_score = float(cfg.get("min_liquidity_score", 0.70))
        self.max_risk_score = float(cfg.get("max_risk_score", 0.40))

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def scan(self, symbols: list[str], horizon_hours: int = 1
             ) -> list[FundingOpportunity]:
        hl_snaps = self.hl.fetch(symbols)
        aster_snaps = self.aster.fetch(symbols) if self.aster.available else {}
        results: list[FundingOpportunity] = []
        for sym in symbols:
            hl = hl_snaps.get(sym)
            asr = aster_snaps.get(sym) if aster_snaps else None
            if hl is None and asr is None:
                continue
            if hl is not None and asr is not None:
                results.append(self._build_cross(sym, hl, asr, horizon_hours))
            else:
                snap = hl or asr
                results.append(self._build_single(sym, snap, horizon_hours))
        return results

    # ----------------------------------------------------------------
    # Builders
    # ----------------------------------------------------------------

    def _build_single(self, sym: str, snap: FundingSnapshot,
                      horizon_hours: int) -> FundingOpportunity:
        funding_bps = snap.funding_rate_bps
        # Direction that *receives* funding :
        if funding_bps > 0:
            direction = "short"
        elif funding_bps < 0:
            direction = "long"
        else:
            direction = "neutral"

        cost_bps_round = 2 * self.cost_bps_per_leg
        gross_bps_per_hour = abs(funding_bps)
        gross_bps_horizon = gross_bps_per_hour * horizon_hours
        net_bps = gross_bps_horizon - cost_bps_round
        net_usd = (net_bps / 10_000.0) * self.notional_usd

        if direction == "neutral":
            decision, reason = DEC_NO_TRADE_LOW_EDGE, "funding_flat"
        elif net_bps <= 0:
            decision, reason = DEC_NO_TRADE_COST_TOO_HIGH, "carry_below_costs"
        elif net_usd < self.min_net_carry_usd:
            decision, reason = DEC_NO_TRADE_LOW_EDGE, "net_usd_below_min"
        else:
            # Single-leg is directional → never recommend live.
            decision, reason = DEC_PAPER_ONLY_SINGLE, "single_leg_directional"

        return FundingOpportunity(
            symbol=sym,
            long_exchange=snap.exchange if direction == "long" else None,
            short_exchange=snap.exchange if direction == "short" else None,
            direction=direction,
            notional_usd=self.notional_usd,
            expected_funding_bps=funding_bps,
            expected_net_bps=net_bps,
            expected_net_usd=net_usd,
            estimated_cost_bps=cost_bps_round,
            mode="single",
            horizon_hours=horizon_hours,
            reason=f"{decision}|{reason}",
            liquidity_score=1.0 if snap.exchange == "hyperliquid" else 0.6,
            risk_score=0.4,    # directional → not zero
        )

    def _build_cross(self, sym: str, hl: FundingSnapshot,
                     aster: FundingSnapshot, horizon_hours: int
                     ) -> FundingOpportunity:
        funding_hl_bps = hl.funding_rate_bps
        funding_a_bps = aster.funding_rate_bps
        spread_bps = funding_hl_bps - funding_a_bps   # per hour

        # Direction logic : we want to SHORT the leg with the higher
        # funding (pays funding when positive) and LONG the leg with the
        # lower funding (receives when negative, or pays less).
        if spread_bps > 0:
            short_exchange = hl.exchange
            long_exchange = aster.exchange
        elif spread_bps < 0:
            short_exchange = aster.exchange
            long_exchange = hl.exchange
        else:
            short_exchange = long_exchange = None

        # Each leg pays one round-trip → 2× cost_bps per leg.
        cost_bps = 4 * self.cost_bps_per_leg
        gross_bps_horizon = abs(spread_bps) * horizon_hours
        net_bps = gross_bps_horizon - cost_bps
        net_usd = (net_bps / 10_000.0) * self.notional_usd

        # Basis : difference of mark prices (in bps relative to mid)
        basis_bps = 0.0
        if hl.mark_price and aster.mark_price and hl.mark_price > 0:
            mid = (hl.mark_price + aster.mark_price) / 2.0
            if mid > 0:
                basis_bps = abs(hl.mark_price - aster.mark_price) / mid * 10_000.0

        # Stub scoring (real scoring would use OI, top-of-book depth, etc.)
        liquidity_score = 0.8 if (hl.open_interest or 0) > 0 else 0.6
        risk_score = 0.3 + min(1.0, basis_bps / max(self.max_basis_bps, 1.0)) * 0.4

        decision = DEC_PAPER_CROSS_CANDIDATE
        reason = "ok"
        if abs(spread_bps) < self.min_funding_spread_bps:
            decision, reason = DEC_NO_TRADE_LOW_EDGE, "spread_too_small"
        elif net_bps <= 0:
            decision, reason = DEC_NO_TRADE_COST_TOO_HIGH, "spread_below_costs"
        elif basis_bps > self.max_basis_bps:
            decision, reason = DEC_NO_TRADE_BASIS_RISK, "basis_too_wide"
        elif liquidity_score < self.min_liquidity_score:
            decision, reason = DEC_NO_TRADE_LIQUIDITY, "liquidity_low"
        # Execution availability : if either leg can't be routed live we
        # downgrade to paper-only.
        if not self.aster.available:
            # Aster adapter unavailable ⇒ cross-exchange is paper-only.
            if decision == DEC_PAPER_CROSS_CANDIDATE:
                reason = "aster_funding_unavailable"
                decision = DEC_EXECUTION_NOT_AVAILABLE

        return FundingOpportunity(
            symbol=sym,
            long_exchange=long_exchange,
            short_exchange=short_exchange,
            direction="cross",
            notional_usd=self.notional_usd,
            expected_funding_bps=spread_bps,
            expected_net_bps=net_bps,
            expected_net_usd=net_usd,
            estimated_cost_bps=cost_bps,
            basis_bps=basis_bps,
            liquidity_score=liquidity_score,
            risk_score=risk_score,
            mode="cross_exchange",
            horizon_hours=horizon_hours,
            reason=f"{decision}|{reason}",
        )

    # ----------------------------------------------------------------
    # Markdown report
    # ----------------------------------------------------------------

    @staticmethod
    def to_markdown(opps: list[FundingOpportunity]) -> str:
        lines = [
            "# Funding Opportunities Report",
            "",
            "| Symbol | Mode | Direction | Funding (bps/h) | Net bps | Net USD | Cost bps | Basis bps | Liq | Risk | Decision |",
            "|--------|------|-----------|-----------------|---------|---------|----------|-----------|-----|------|----------|",
        ]
        for o in sorted(opps, key=lambda x: x.expected_net_usd, reverse=True):
            lines.append(
                f"| {o.symbol} | {o.mode} | {o.direction} | "
                f"{o.expected_funding_bps:.3f} | {o.expected_net_bps:.3f} | "
                f"{o.expected_net_usd:.4f} | {o.estimated_cost_bps:.2f} | "
                f"{o.basis_bps:.2f} | {o.liquidity_score:.2f} | "
                f"{o.risk_score:.2f} | {o.reason} |"
            )
        return "\n".join(lines)
