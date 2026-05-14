"""Tests for `risk/funding_risk_manager.py`."""
from dataclasses import dataclass

from risk.funding_risk_manager import (
    FundingRiskLimits,
    FundingRiskManager,
    FundingRiskState,
)


@dataclass
class _Opp:
    symbol: str = "BTC"
    long_exchange: str = "aster"
    short_exchange: str = "hyperliquid"
    direction: str = "cross"
    mode: str = "cross_exchange"
    notional_usd: float = 25.0
    expected_funding_bps: float = 5.0
    expected_net_bps: float = 3.0
    expected_net_usd: float = 0.10
    estimated_cost_bps: float = 2.0
    basis_bps: float = 10.0
    liquidity_score: float = 0.9
    risk_score: float = 0.2


def test_ok_when_all_gates_pass():
    m = FundingRiskManager()
    ok, reason = m.check(_Opp())
    assert ok, reason


def test_blocks_live_when_not_allowed():
    m = FundingRiskManager(FundingRiskLimits(allow_live=False))
    ok, reason = m.check(_Opp(), live_requested=True)
    assert not ok
    assert reason == "live_blocked"


def test_blocks_zero_notional():
    m = FundingRiskManager()
    ok, _ = m.check(_Opp(notional_usd=0))
    assert not ok


def test_blocks_low_carry():
    m = FundingRiskManager()
    ok, reason = m.check(_Opp(expected_net_usd=0.0))
    assert not ok
    assert reason == "expected_net_carry_too_low"


def test_blocks_wide_basis():
    m = FundingRiskManager(FundingRiskLimits(max_basis_bps=5.0))
    ok, reason = m.check(_Opp(basis_bps=10.0))
    assert not ok
    assert reason == "basis_too_wide"


def test_blocks_missing_cross_leg():
    m = FundingRiskManager()
    ok, reason = m.check(_Opp(short_exchange=None))
    assert not ok
    assert reason == "cross_exchange_leg_missing"


def test_blocks_total_exposure():
    m = FundingRiskManager(FundingRiskLimits(max_total_funding_notional=30))
    state = FundingRiskState(open_notional_total=20)
    ok, reason = m.check(_Opp(notional_usd=15), state)
    assert not ok
    assert reason == "max_total_funding_notional_exceeded"
