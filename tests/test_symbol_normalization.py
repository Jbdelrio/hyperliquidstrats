"""
tests/test_symbol_normalization.py — Tests for symbol normalization in all adapters.
"""
import pytest
from exchanges.hyperliquid_adapter import HyperliquidAdapter
from exchanges.binance_adapter import BinanceAdapter
from exchanges.bitget_adapter import BitgetAdapter


_HL = HyperliquidAdapter()
_BN = BinanceAdapter()
_BG = BitgetAdapter()


@pytest.mark.parametrize("raw,expected", [
    ("BTC",         "BTC"),
    ("btc",         "BTC"),
    ("BTC-USD",     "BTC"),
    ("BTC/USDT",    "BTC"),
    ("BTCUSDT",     "BTC"),   # HL strips USDT suffix
    ("ETH",         "ETH"),
    ("SOL-USD",     "SOL"),
])
def test_hyperliquid_normalize(raw, expected):
    assert _HL.normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("BTC",         "BTCUSDT"),
    ("btc",         "BTCUSDT"),
    ("BTC-USD",     "BTCUSDT"),
    ("BTC-USDT",    "BTCUSDT"),
    ("BTC/USDT",    "BTCUSDT"),
    ("BTCUSDT",     "BTCUSDT"),
    ("ETH",         "ETHUSDT"),
    ("SOL",         "SOLUSDT"),
])
def test_binance_normalize(raw, expected):
    assert _BN.normalize_symbol(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("BTC",         "BTCUSDT"),
    ("btc",         "BTCUSDT"),
    ("BTC-USD",     "BTCUSDT"),
    ("BTC-USDT",    "BTCUSDT"),
    ("BTC/USDT",    "BTCUSDT"),
    ("BTCUSDT",     "BTCUSDT"),
    ("ETH",         "ETHUSDT"),
])
def test_bitget_normalize(raw, expected):
    assert _BG.normalize_symbol(raw) == expected
