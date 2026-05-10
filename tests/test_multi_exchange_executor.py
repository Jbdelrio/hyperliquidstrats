"""
tests/test_multi_exchange_executor.py — Tests for the multi-exchange executor.
"""
import os
import pytest

os.environ["GLOBAL_LIVE_TRADING"]     = "false"
os.environ["HYPERLIQUID_LIVE_TRADING"] = "false"
os.environ["BINANCE_ENABLED"]          = "false"
os.environ["BITGET_ENABLED"]           = "false"

from exchanges.schemas import OrderRequest
from execution.multi_exchange_executor import MultiExchangeExecutor


def _req(exchange="hyperliquid", symbol="BTC", side="BUY", size=0.01):
    return OrderRequest(
        exchange=exchange, symbol=symbol,
        side=side, order_type="MARKET", size=size,
    )


def test_unknown_exchange_returns_error():
    exec_ = MultiExchangeExecutor()
    resp = exec_.place_order(_req(exchange="unknown_exchange"))
    assert resp.status == "error"


def test_hyperliquid_live_disabled_blocked():
    exec_ = MultiExchangeExecutor()
    resp = exec_.place_order(_req(exchange="hyperliquid"))
    assert resp.status == "blocked_live_disabled"


def test_binance_disabled_returns_error_or_blocked():
    exec_ = MultiExchangeExecutor()
    resp = exec_.place_order(_req(exchange="binance"))
    # Either None (unknown because disabled) or blocked
    assert resp.status in ("error", "blocked_live_disabled")


def test_cancel_unknown_exchange():
    exec_ = MultiExchangeExecutor()
    result = exec_.cancel_order("unknown_exchange", "ord_123", "BTC")
    assert result.get("status") == "error"
