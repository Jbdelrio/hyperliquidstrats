#!/usr/bin/env python
"""
scan_funding_opportunities.py — CLI scanner for funding arbitrage.

Usage :
    python scripts/scan_funding_opportunities.py \
        --exchanges hyperliquid,aster \
        --symbols BTC,ETH,SOL,HYPE \
        --out reports/funding_opportunities.md

The Aster adapter is currently a no-op (no documented endpoint wired
yet). When that's missing, every cross-exchange row is downgraded to
EXECUTION_NOT_AVAILABLE and single-leg rows are PAPER_ONLY_SINGLE_EXCHANGE.

No order is placed. No live API is hit beyond the funding REST call to
Hyperliquid (cached by the adapter).
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
from pathlib import Path

# Make repo importable when invoked from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from data.exchange_adapters.hyperliquid_funding import HyperliquidFundingAdapter
from data.exchange_adapters.aster_funding import AsterFundingAdapter
from research.funding_opportunity_scanner import FundingOpportunityScanner
from monitoring.funding_logger import (
    FundingSnapshotLogger,
    FundingOpportunityLogger,
)


def _parse_args():
    p = argparse.ArgumentParser(
        description="Scan funding opportunities (paper/research only).")
    p.add_argument("--exchanges", default="hyperliquid,aster",
                   help="Comma-separated list (currently 'hyperliquid' is the only one wired).")
    p.add_argument("--symbols", default="BTC,ETH,SOL,HYPE")
    p.add_argument("--horizon-hours", type=int, default=8)
    p.add_argument("--notional-usd", type=float, default=25.0)
    p.add_argument("--out", default="reports/funding_opportunities.md")
    p.add_argument("--log-snapshots",
                   default="logs/funding_snapshots.csv")
    p.add_argument("--log-opportunities",
                   default="logs/funding_opportunities.csv")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    exchanges = {e.strip().lower() for e in args.exchanges.split(",") if e.strip()}
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]

    hl = HyperliquidFundingAdapter() if "hyperliquid" in exchanges else None
    aster = AsterFundingAdapter() if "aster" in exchanges else None

    snap_logger = FundingSnapshotLogger(args.log_snapshots)
    opp_logger = FundingOpportunityLogger(args.log_opportunities)

    # Snapshots
    snaps: list = []
    if hl is not None:
        hl_snaps = hl.fetch(symbols, force=True)
        snaps.extend(hl_snaps.values())
    if aster is not None and aster.available:
        a_snaps = aster.fetch(symbols, force=True)
        snaps.extend(a_snaps.values())
    if snaps:
        snap_logger.log_snapshots(snaps)

    # Opportunities
    scanner = FundingOpportunityScanner(
        hl_adapter=hl,
        aster_adapter=aster,
        config={"notional_usd": args.notional_usd},
    )
    opps = scanner.scan(symbols, horizon_hours=args.horizon_hours)

    # Log + report
    rows = []
    for o in opps:
        row = o.as_log_row()
        # cross-exchange convenience fields
        row.setdefault("funding_hl", "")
        row.setdefault("funding_aster", "")
        row["spread_bps"] = o.expected_funding_bps if o.mode == "cross_exchange" else ""
        row["decision"] = o.reason.split("|", 1)[0]
        rows.append(row)
    if rows:
        opp_logger.log(rows)

    md = FundingOpportunityScanner.to_markdown(opps)
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    if not args.quiet:
        print(md)
        print()
        print(f"Wrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
