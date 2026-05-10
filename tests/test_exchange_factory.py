"""
tests/test_exchange_factory.py — Unit tests for exchange factory.
"""
import os
import pytest


def test_hyperliquid_adapter_available():
    from exchanges.factory import get_exchange
    adapter = get_exchange("hyperliquid")
    assert adapter is not None
    assert adapter.name == "hyperliquid"


def test_binance_disabled_returns_none():
    os.environ["BINANCE_ENABLED"] = "false"
    from exchanges import factory as f
    f._REGISTRY.pop("binance", None)
    adapter = f.get_exchange("binance")
    assert adapter is None


def test_bitget_disabled_returns_none():
    os.environ["BITGET_ENABLED"] = "false"
    from exchanges import factory as f
    f._REGISTRY.pop("bitget", None)
    adapter = f.get_exchange("bitget")
    assert adapter is None


def test_unknown_exchange_returns_none():
    from exchanges.factory import get_exchange
    assert get_exchange("unknown_exchange_xyz") is None


def test_get_enabled_exchanges_default():
    os.environ["ENABLED_EXCHANGES"] = "hyperliquid"
    from exchanges import factory as f
    f._REGISTRY.clear()
    adapters = f.get_enabled_exchanges()
    names = [a.name for a in adapters]
    assert "hyperliquid" in names


def test_binance_live_disabled_by_default():
    os.environ["BINANCE_ENABLED"] = "true"
    os.environ["BINANCE_LIVE_TRADING"] = "false"
    os.environ["GLOBAL_LIVE_TRADING"] = "false"
    from exchanges.binance_adapter import BinanceAdapter
    a = BinanceAdapter()
    assert a.is_live_trading_enabled() is False


def test_bitget_live_disabled_by_default():
    os.environ["BITGET_ENABLED"] = "true"
    os.environ["BITGET_LIVE_TRADING"] = "false"
    os.environ["GLOBAL_LIVE_TRADING"] = "false"
    from exchanges.bitget_adapter import BitgetAdapter
    a = BitgetAdapter()
    assert a.is_live_trading_enabled() is False


def test_hyperliquid_live_disabled_by_default():
    os.environ["HYPERLIQUID_LIVE_TRADING"] = "false"
    os.environ["GLOBAL_LIVE_TRADING"] = "false"
    from exchanges.hyperliquid_adapter import HyperliquidAdapter
    a = HyperliquidAdapter()
    assert a.is_live_trading_enabled() is False
