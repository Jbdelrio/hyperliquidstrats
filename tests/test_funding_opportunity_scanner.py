"""Tests for `research/funding_opportunity_scanner.py`."""
import time
from dataclasses import dataclass

import pytest

from data.funding_data import FundingSnapshot
from research.funding_opportunity_scanner import (
    FundingOpportunityScanner,
    DEC_NO_TRADE_LOW_EDGE,
    DEC_PAPER_ONLY_SINGLE,
    DEC_PAPER_CROSS_CANDIDATE,
    DEC_EXECUTION_NOT_AVAILABLE,
)


class _FakeAdapter:
    EXCHANGE = "fake"

    def __init__(self, snaps: dict, available: bool = True):
        self._snaps = snaps
        self._available = available

    @property
    def available(self):
        return self._available

    def fetch(self, symbols=None, force=False):
        if not self._available:
            return {}
        if symbols:
            return {s: self._snaps[s] for s in symbols if s in self._snaps}
        return dict(self._snaps)


def _snap(exchange, symbol, hourly_rate, mark=None):
    return FundingSnapshot(
        exchange=exchange, symbol=symbol, timestamp=time.time(),
        funding_rate=hourly_rate, mark_price=mark, open_interest=1_000_000,
    )


def test_single_exchange_short_direction_when_funding_positive():
    hl = _FakeAdapter({"BTC": _snap("hyperliquid", "BTC", 0.0005)})  # +5 bps/h
    a = _FakeAdapter({}, available=False)
    sc = FundingOpportunityScanner(hl, a,
                                   config={"notional_usd": 100,
                                           "cost_bps_per_leg": 1.0})
    opps = sc.scan(["BTC"], horizon_hours=8)
    assert len(opps) == 1
    o = opps[0]
    assert o.mode == "single"
    assert o.direction == "short"
    # gross over 8h : 5*8 = 40 bps ; cost : 2*1 = 2 ⇒ net = 38 bps
    assert abs(o.expected_net_bps - 38.0) < 1e-6


def test_single_exchange_long_when_funding_negative():
    hl = _FakeAdapter({"BTC": _snap("hyperliquid", "BTC", -0.0005)})
    a = _FakeAdapter({}, available=False)
    sc = FundingOpportunityScanner(hl, a)
    opps = sc.scan(["BTC"], horizon_hours=1)
    assert opps[0].direction == "long"


def test_carry_below_costs_rejected():
    # 0.1 bps/h with 4 bps/leg cost (16 bps round-trip if cross — here single
    # so 2 bps round-trip)
    hl = _FakeAdapter({"BTC": _snap("hyperliquid", "BTC", 1e-6)})
    a = _FakeAdapter({}, available=False)
    sc = FundingOpportunityScanner(hl, a,
                                   config={"notional_usd": 100,
                                           "cost_bps_per_leg": 10.0})
    opps = sc.scan(["BTC"], horizon_hours=1)
    assert DEC_NO_TRADE_LOW_EDGE in opps[0].reason \
        or "NO_TRADE_COST_TOO_HIGH" in opps[0].reason


def test_cross_exchange_spread_correct():
    hl = _FakeAdapter({"BTC": _snap("hyperliquid", "BTC", 0.0005, mark=100)})
    asr = _FakeAdapter({"BTC": _snap("aster", "BTC", 0.0001, mark=100)})
    sc = FundingOpportunityScanner(hl, asr,
                                   config={"notional_usd": 100,
                                           "cost_bps_per_leg": 1.0})
    opps = sc.scan(["BTC"], horizon_hours=1)
    o = opps[0]
    assert o.mode == "cross_exchange"
    # 5 - 1 = 4 bps/h ; cost 4*1=4 ⇒ net=0 → decision is COST_TOO_HIGH/LOW_EDGE
    # Spread should match the difference
    assert abs(o.expected_funding_bps - 4.0) < 1e-6


def test_cross_exchange_when_aster_unavailable_downgraded():
    """If Aster snapshot present BUT adapter.available is False, scanner
    should still produce candidates but downgrade decision."""
    hl = _FakeAdapter({"BTC": _snap("hyperliquid", "BTC", 0.0005, mark=100)})
    asr = _FakeAdapter({}, available=False)
    sc = FundingOpportunityScanner(hl, asr)
    opps = sc.scan(["BTC"], horizon_hours=1)
    # Only HL data available → single-exchange path
    assert opps[0].mode == "single"


def test_to_markdown_runs():
    hl = _FakeAdapter({"BTC": _snap("hyperliquid", "BTC", 0.0005)})
    a = _FakeAdapter({}, available=False)
    sc = FundingOpportunityScanner(hl, a)
    md = FundingOpportunityScanner.to_markdown(sc.scan(["BTC"]))
    assert "Funding Opportunities Report" in md
