"""Tests for the Phase 1 hardening of OrderbookManager."""
import asyncio
import time

import pytest

from data.orderbook_manager import OrderbookManager, OrderBook


@pytest.fixture
def obm():
    o = OrderbookManager(["BTC"])
    # Run in a fresh event loop so the async Queue is attached.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    yield o
    loop.close()


def test_health_snapshot_has_all_fields(obm):
    snap = obm.health_snapshot()
    expected_top = {
        "ts", "running", "symbols", "reconnections",
        "book_updates_count", "trade_events_count",
        "dropped_book_updates_count", "dropped_trade_events_count",
        "json_parse_errors_count", "invalid_book_count",
        "crossed_book_count", "queue_drops", "per_symbol",
    }
    assert expected_top <= set(snap.keys())
    assert "BTC" in snap["per_symbol"]
    ps = snap["per_symbol"]["BTC"]
    for k in ("book_updates", "trade_events",
              "book_updates_per_sec", "trades_per_sec",
              "p95_latency_ms", "spread_bps_mean",
              "is_book_stale", "is_trade_stale"):
        assert k in ps


def test_crossed_book_rejected(obm):
    msg = {
        "coin": "BTC",
        "time": int(time.time() * 1000),
        # bid > ask → crossed
        "levels": [
            [{"px": "101.0", "sz": "1.0"}],
            [{"px": "100.0", "sz": "1.0"}],
        ],
    }
    obm._on_l2book(msg)
    assert obm.crossed_book_count == 1
    assert obm.invalid_book_count == 1
    assert obm.get_book("BTC") is None


def test_trade_kept_when_book_absent(obm):
    msg = [{"coin": "BTC", "px": "100.5", "sz": "0.5", "side": "B",
            "time": int(time.time() * 1000)}]
    obm._on_trades(msg)
    # Trade buffer should have the trade even though no book is set.
    assert len(obm._trades["BTC"]) == 1
    assert obm.trade_events_count == 1
    assert obm.dropped_trade_events_count == 0


def test_queue_drops_counted(obm):
    # Fill the trade queue to capacity, then push another.
    while True:
        try:
            obm._trade_q.put_nowait("dummy")
        except asyncio.QueueFull:
            break
    obm._on_trades([{"coin": "BTC", "px": "100", "sz": "1", "side": "B",
                     "time": int(time.time() * 1000)}])
    assert obm.dropped_trade_events_count >= 1
