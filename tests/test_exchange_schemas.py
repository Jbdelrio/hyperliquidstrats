"""
tests/test_exchange_schemas.py — Unit tests for exchange data schemas.
"""
import pytest
from exchanges.schemas import ExchangeTicker, ExchangeOrderbook, OrderRequest, OrderResponse


def test_ticker_spread_computed():
    t = ExchangeTicker(exchange="test", symbol="BTC", timestamp="2026-01-01",
                       bid=50000.0, ask=50005.0)
    assert t.spread_bps == pytest.approx(1.0, abs=0.01)


def test_ticker_mid_computed():
    t = ExchangeTicker(exchange="test", symbol="BTC", timestamp="2026-01-01",
                       bid=50000.0, ask=50010.0)
    assert t.mid == pytest.approx(50005.0)


def test_ticker_no_bid_ask():
    t = ExchangeTicker(exchange="test", symbol="BTC", timestamp="2026-01-01")
    assert t.spread_bps is None
    assert t.mid is None


def test_orderbook_spread_computed():
    ob = ExchangeOrderbook(
        exchange="test", symbol="BTC", timestamp="2026-01-01",
        bids=[[50000.0, 1.0], [49999.0, 2.0]],
        asks=[[50005.0, 1.0], [50006.0, 2.0]],
    )
    assert ob.spread_bps == pytest.approx(1.0, abs=0.01)


def test_orderbook_imbalance_computed():
    ob = ExchangeOrderbook(
        exchange="test", symbol="BTC", timestamp="2026-01-01",
        bids=[[50000.0, 2.0]],
        asks=[[50005.0, 1.0]],
    )
    # 2/(2+1) - 0.5 = 0.1667
    assert ob.imbalance == pytest.approx(0.1667, abs=0.001)


def test_orderbook_empty():
    ob = ExchangeOrderbook(exchange="test", symbol="BTC", timestamp="2026-01-01")
    assert ob.spread_bps is None
    assert ob.imbalance is None


def test_order_request_fields():
    req = OrderRequest(
        exchange="hyperliquid", symbol="BTC",
        side="BUY", order_type="MARKET", size=0.01,
    )
    assert req.reduce_only is False
    assert req.strategy_id is None


def test_order_response_defaults():
    resp = OrderResponse(exchange="test", symbol="BTC", status="filled")
    assert resp.filled_size == 0.0
    assert resp.avg_price is None
