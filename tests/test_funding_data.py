"""Tests for funding dataclasses + adapters."""
import time

from data.funding_data import FundingSnapshot, FundingOpportunity
from data.exchange_adapters.aster_funding import AsterFundingAdapter


def test_funding_snapshot_bps_conversion():
    s = FundingSnapshot(
        exchange="hyperliquid", symbol="BTC", timestamp=time.time(),
        funding_rate=0.0001,  # 1 bps hourly
    )
    assert abs(s.funding_rate_bps - 1.0) < 1e-9


def test_funding_snapshot_handles_nan():
    s = FundingSnapshot(
        exchange="hyperliquid", symbol="BTC", timestamp=time.time(),
        funding_rate=float("nan"),
    )
    # NaN propagates — that's fine, no crash
    import math
    assert math.isnan(s.funding_rate_bps)


def test_funding_opportunity_as_log_row_complete():
    o = FundingOpportunity(
        symbol="ETH",
        long_exchange="aster", short_exchange="hyperliquid",
        direction="cross",
        notional_usd=25.0,
        expected_funding_bps=5.0,
        expected_net_bps=2.0,
        expected_net_usd=0.05,
        estimated_cost_bps=3.0,
        mode="cross_exchange",
        horizon_hours=8,
    )
    row = o.as_log_row()
    assert row["symbol"] == "ETH"
    assert row["long_exchange"] == "aster"
    assert row["short_exchange"] == "hyperliquid"


def test_aster_adapter_unavailable_by_default():
    a = AsterFundingAdapter()
    assert a.available is False
    assert a.fetch(["BTC"]) == {}
