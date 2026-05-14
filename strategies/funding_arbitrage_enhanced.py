"""
funding_arbitrage_enhanced.py — Cross-exchange + single-exchange funding
arbitrage strategy. **DISABLED + research_only by default.**

Modes
-----
* `single-exchange` : Hyperliquid only — directional carry, never delta-neutral.
* `cross-exchange`  : Hyperliquid ↔ Aster funding spread — paper-only until
  Aster execution adapter exists.

This strategy NEVER opens an order. In `research_only=true` mode it just
periodically scans, logs opportunities, and reports its current view via
`get_calibration_data`.

When (and only when) :
  - `trade_enabled=true`,
  - `research_only=false`,
  - and (for cross-exchange) `cross_exchange_paper_only=false`
       AND `allow_live=true` AND both legs' execution adapters are wired,
it could eventually emit `StrategyDecision`. None of these are wired in
this codebase. Until then `on_bar_minute` returns None.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from data.exchange_adapters.hyperliquid_funding import HyperliquidFundingAdapter
from data.exchange_adapters.aster_funding import AsterFundingAdapter
from research.funding_opportunity_scanner import FundingOpportunityScanner
from monitoring.funding_logger import (
    FundingSnapshotLogger,
    FundingOpportunityLogger,
)
from risk.funding_risk_manager import FundingRiskManager, FundingRiskLimits
from strategies.base_strategy import (
    BarData,
    BaseStrategy,
    StrategyConfig,
    StrategyDecision,
)

log = logging.getLogger(__name__)


class FundingArbitrageEnhanced(BaseStrategy):

    DEFAULT_PARAMS = dict(
        trade_enabled=False,
        research_only=True,
        cross_exchange_paper_only=True,
        allow_live=False,
        refresh_interval_s=60.0,
        horizon_hours=8,
        min_funding_spread_bps=3.0,
        min_net_carry_bps=2.0,
        min_net_carry_usd=0.05,
        max_basis_bps=30.0,
        max_notional_usd=25.0,
        min_liquidity_score=0.70,
        max_risk_score=0.40,
        notional_usd=25.0,
    )

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        merged = dict(self.DEFAULT_PARAMS)
        merged.update(config.params or {})
        config.params = merged

        self.hl_adapter = HyperliquidFundingAdapter(
            min_refresh_interval_s=float(merged["refresh_interval_s"]),
        )
        self.aster_adapter = AsterFundingAdapter(
            min_refresh_interval_s=float(merged["refresh_interval_s"]),
        )
        self.scanner = FundingOpportunityScanner(
            hl_adapter=self.hl_adapter,
            aster_adapter=self.aster_adapter,
            config={
                "notional_usd": float(merged["notional_usd"]),
                "min_funding_spread_bps": float(merged["min_funding_spread_bps"]),
                "min_net_carry_bps": float(merged["min_net_carry_bps"]),
                "min_net_carry_usd": float(merged["min_net_carry_usd"]),
                "max_basis_bps": float(merged["max_basis_bps"]),
                "min_liquidity_score": float(merged["min_liquidity_score"]),
                "max_risk_score": float(merged["max_risk_score"]),
            },
        )
        self.risk = FundingRiskManager(FundingRiskLimits(
            max_notional_per_trade=float(merged["max_notional_usd"]),
            min_expected_net_carry_usd=float(merged["min_net_carry_usd"]),
            max_basis_bps=float(merged["max_basis_bps"]),
            min_liquidity_score=float(merged["min_liquidity_score"]),
            max_risk_score=float(merged["max_risk_score"]),
            min_funding_spread_bps=float(merged["min_funding_spread_bps"]),
            allow_live=bool(merged["allow_live"]),
        ))
        self._snapshot_logger = FundingSnapshotLogger()
        self._opportunity_logger = FundingOpportunityLogger()
        self._last_scan_ts: float = 0.0
        self._last_opportunities: list = []
        self._last_view: dict[str, dict] = {}

    # --- Standard hooks --------------------------------------------------

    def on_orderbook_update(self, symbol, book, ts):
        return None

    def on_trade_update(self, symbol, trade, ts):
        return None

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float
                      ) -> Optional[StrategyDecision]:
        # Scan no more than once per refresh_interval_s, regardless of
        # which symbol triggers the bar.
        p = self.config.params
        if ts - self._last_scan_ts < float(p["refresh_interval_s"]):
            return None
        self._last_scan_ts = ts
        self._scan(ts)
        # No order ever emitted from this strategy.
        return None

    # --- Internals -------------------------------------------------------

    def _scan(self, ts: float) -> None:
        p = self.config.params
        symbols = list(self.config.coins)
        if not symbols:
            return
        try:
            hl_snaps = self.hl_adapter.fetch(symbols)
        except Exception as e:
            log.debug("funding HL fetch error: %s", e)
            hl_snaps = {}
        try:
            aster_snaps = self.aster_adapter.fetch(symbols) if self.aster_adapter.available else {}
        except Exception:
            aster_snaps = {}

        all_snaps = list(hl_snaps.values()) + list(aster_snaps.values())
        if all_snaps:
            try:
                self._snapshot_logger.log_snapshots(all_snaps)
            except Exception as e:
                log.debug("snapshot logger error: %s", e)

        try:
            opps = self.scanner.scan(symbols, horizon_hours=int(p["horizon_hours"]))
        except Exception as e:
            log.warning("Funding scan failed: %s", e)
            opps = []
        self._last_opportunities = opps

        rows = []
        for o in opps:
            row = o.as_log_row()
            row["decision"] = o.reason.split("|", 1)[0]
            row["spread_bps"] = o.expected_funding_bps if o.mode == "cross_exchange" else ""
            rows.append(row)
        if rows:
            try:
                self._opportunity_logger.log(rows)
            except Exception as e:
                log.debug("opportunity logger error: %s", e)

        # Refresh per-symbol GUI view
        self._last_view = {}
        for o in opps:
            ok, reason = self.risk.check(o, live_requested=False)
            self._last_view[o.symbol] = {
                "mode": o.mode,
                "direction": o.direction,
                "funding_bps": o.expected_funding_bps,
                "net_bps": o.expected_net_bps,
                "net_usd": o.expected_net_usd,
                "cost_bps": o.estimated_cost_bps,
                "basis_bps": o.basis_bps,
                "liquidity_score": o.liquidity_score,
                "risk_score": o.risk_score,
                "decision": o.reason,
                "risk_ok": ok,
                "risk_reason": reason,
            }

    def get_calibration_data(self, symbol: str) -> dict:
        return dict(self._last_view.get(symbol, {}))
