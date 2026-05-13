"""
tests/test_portfolio_risk_manager.py — Phase 3 portfolio-level limits.

Run: python -m pytest tests/test_portfolio_risk_manager.py -v
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from risk.portfolio_risk_manager import (
    PortfolioRiskManager,
    STRATEGY_FAMILIES,
    family_of,
)


# ---------------------------------------------------------------------------
# Family mapping
# ---------------------------------------------------------------------------

def test_family_mapping_known_strategies():
    assert family_of("MomentumLS")            == "momentum"
    assert family_of("RotationMomentum")      == "momentum"
    assert family_of("BreakoutControlled")    == "breakout"
    assert family_of("DonchianTrend")         == "breakout"
    assert family_of("VolatilityRegimeBreakout") == "breakout"
    assert family_of("MeanReversionKalman")   == "mean_reversion"
    assert family_of("RSIBollingerReversion") == "mean_reversion"
    assert family_of("RelativeValue")         == "mean_reversion"
    assert family_of("S8EMS")                 == "market_making"
    assert family_of("OBImbalanceScalper")    == "market_making"
    assert family_of("FundingArbitrage")      == "funding"
    assert family_of("FundingCarryHedged")    == "funding"
    assert family_of("SpotPerpBasis")         == "funding"
    assert family_of("MetaAlpha")             == "meta"


def test_family_unknown_returns_other():
    assert family_of("DoesNotExist") == "other"


# ---------------------------------------------------------------------------
# Coin concentration
# ---------------------------------------------------------------------------

def test_coin_limit_blocks_concentration():
    """35% coin cap on $1000 capital → $350 cap on a single coin."""
    prm = PortfolioRiskManager(
        max_coin_exposure_pct=0.35,
        max_net_exposure_pct=0.99,
        max_family_exposure_pct=0.99,
        max_correlated_same_dir=99,
    )
    prm.register_open("MomentumLS", "BTC", "BUY", 200.0)

    # Another $200 = $400 > $350 → block
    ok, reason = prm.can_open("BreakoutControlled", "BTC", "BUY",
                              notional=200.0, total_capital=1000.0)
    assert not ok
    assert "portfolio_coin_limit" in reason

    # $100 more = $300 ≤ $350 → allow
    ok2, _ = prm.can_open("BreakoutControlled", "BTC", "BUY",
                          notional=100.0, total_capital=1000.0)
    assert ok2


# ---------------------------------------------------------------------------
# Net exposure
# ---------------------------------------------------------------------------

def test_net_limit_blocks_excess_long():
    prm = PortfolioRiskManager(
        max_coin_exposure_pct=0.99,
        max_net_exposure_pct=0.50,
        max_family_exposure_pct=0.99,
        max_correlated_same_dir=99,
    )
    # 4 longs of $100 each = +$400 net
    prm.register_open("MomentumLS", "BTC", "BUY", 100.0)
    prm.register_open("MomentumLS", "ETH", "BUY", 100.0)
    prm.register_open("BreakoutControlled", "SOL", "BUY", 100.0)
    prm.register_open("DonchianTrend", "AVAX", "BUY", 100.0)

    # Adding another $200 long → $600 > $500 → block
    ok, reason = prm.can_open("MomentumLS", "LINK", "BUY",
                              notional=200.0, total_capital=1000.0)
    assert not ok
    assert "portfolio_net_limit" in reason

    # A short of $200 reduces net → allow
    ok2, _ = prm.can_open("MomentumLS", "LINK", "SELL",
                          notional=200.0, total_capital=1000.0)
    assert ok2


# ---------------------------------------------------------------------------
# Family exposure
# ---------------------------------------------------------------------------

def test_family_limit_blocks_concentration():
    """40% family cap on $1000 → $400 per family."""
    prm = PortfolioRiskManager(
        max_coin_exposure_pct=0.99,
        max_net_exposure_pct=0.99,
        max_family_exposure_pct=0.40,
        max_correlated_same_dir=99,
    )
    # MomentumLS and RotationMomentum both belong to "momentum" family
    prm.register_open("MomentumLS", "BTC", "BUY", 200.0)
    prm.register_open("RotationMomentum", "ETH", "BUY", 150.0)
    # = $350 in momentum

    # Adding $100 → $450 > $400 → block
    ok, reason = prm.can_open("MomentumLS", "SOL", "BUY",
                              notional=100.0, total_capital=1000.0)
    assert not ok
    assert "portfolio_family_limit" in reason
    assert "momentum" in reason

    # But a breakout strategy can still trade (different family)
    ok2, _ = prm.can_open("BreakoutControlled", "SOL", "BUY",
                          notional=200.0, total_capital=1000.0)
    assert ok2


# ---------------------------------------------------------------------------
# Correlated same-direction
# ---------------------------------------------------------------------------

def test_correlated_same_dir_limit():
    """max 2 longs on the same coin across strategies."""
    prm = PortfolioRiskManager(
        max_coin_exposure_pct=0.99,
        max_net_exposure_pct=0.99,
        max_family_exposure_pct=0.99,
        max_correlated_same_dir=2,
    )
    prm.register_open("MomentumLS", "BTC", "BUY", 50.0)
    prm.register_open("BreakoutControlled", "BTC", "BUY", 50.0)

    # Third long on BTC → blocked
    ok, reason = prm.can_open("DonchianTrend", "BTC", "BUY",
                              notional=50.0, total_capital=1000.0)
    assert not ok
    assert "portfolio_correlated_limit" in reason

    # But a SHORT on BTC is fine (opposite direction)
    ok2, _ = prm.can_open("RSIBollingerReversion", "BTC", "SELL",
                          notional=50.0, total_capital=1000.0)
    assert ok2


# ---------------------------------------------------------------------------
# Lifecycle: register_open / register_close
# ---------------------------------------------------------------------------

def test_register_close_frees_exposure():
    prm = PortfolioRiskManager(
        max_coin_exposure_pct=0.20,    # tight: $200 max
        max_net_exposure_pct=0.99,
        max_family_exposure_pct=0.99,
        max_correlated_same_dir=99,
    )
    prm.register_open("MomentumLS", "BTC", "BUY", 150.0)

    # Adding $100 → $250 > $200 → block
    ok, _ = prm.can_open("BreakoutControlled", "BTC", "BUY",
                         notional=100.0, total_capital=1000.0)
    assert not ok

    # Close the first position → exposure drops to 0 → allow
    prm.register_close("MomentumLS", "BTC", "BUY", 150.0)
    ok2, _ = prm.can_open("BreakoutControlled", "BTC", "BUY",
                          notional=100.0, total_capital=1000.0)
    assert ok2


def test_snapshot_returns_full_state():
    prm = PortfolioRiskManager()
    prm.register_open("MomentumLS", "BTC", "BUY", 100.0)
    prm.register_open("MomentumLS", "ETH", "SELL", 50.0)
    prm.register_open("S8EMS", "BTC", "BUY", 30.0)

    snap = prm.snapshot(total_capital=1000.0)
    assert snap["positions"] == 3
    assert snap["coin_exposure_usd"]["BTC"]  == pytest.approx(130.0)
    assert snap["coin_exposure_usd"]["ETH"]  == pytest.approx(50.0)
    assert snap["long_usd"]                  == pytest.approx(130.0)
    assert snap["short_usd"]                 == pytest.approx(50.0)
    assert snap["net_usd"]                   == pytest.approx(80.0)
    assert snap["family_exposure_usd"]["momentum"]    == pytest.approx(150.0)
    assert snap["family_exposure_usd"]["market_making"] == pytest.approx(30.0)


def test_zero_total_capital_allows():
    """Edge case: total_capital=0 should not block (no denominator)."""
    prm = PortfolioRiskManager()
    ok, _ = prm.can_open("MomentumLS", "BTC", "BUY",
                         notional=100.0, total_capital=0.0)
    assert ok


def test_reset_clears_positions():
    prm = PortfolioRiskManager()
    prm.register_open("MomentumLS", "BTC", "BUY", 100.0)
    prm.register_open("MomentumLS", "ETH", "BUY", 100.0)
    assert len(prm._positions) == 2
    prm.reset()
    assert len(prm._positions) == 0
