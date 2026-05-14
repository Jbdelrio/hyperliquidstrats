"""Tests for `strategies/funding_arbitrage_enhanced.py`."""
import time

from strategies.base_strategy import BarData, StrategyConfig
from strategies.funding_arbitrage_enhanced import FundingArbitrageEnhanced


def _cfg(extra=None):
    return StrategyConfig(
        name="FundingArbEnhanced",
        enabled=True,
        capital_allocated_usd=100,
        max_positions=1,
        max_position_size_usd=25,
        coins=["BTC"],
        params=dict(extra or {}),
    )


def test_initializes_research_only_by_default():
    s = FundingArbitrageEnhanced(_cfg())
    assert s.config.params["research_only"] is True
    assert s.config.params["trade_enabled"] is False
    assert s.config.params["allow_live"] is False


def test_never_emits_decisions_in_research_only(monkeypatch):
    s = FundingArbitrageEnhanced(_cfg())

    # Patch adapters to avoid REST calls
    s.hl_adapter.fetch = lambda symbols=None, force=False: {}
    s.aster_adapter._available = False
    bar = BarData(symbol="BTC", ts=time.time(), open=100, high=101, low=99,
                  close=100, volume_usd=1000, return_1m=0.0)
    out = s.on_bar_minute("BTC", bar, time.time())
    assert out is None


def test_orderbook_and_trade_hooks_noop():
    s = FundingArbitrageEnhanced(_cfg())
    assert s.on_orderbook_update("BTC", None, time.time()) is None
    assert s.on_trade_update("BTC", None, time.time()) is None
