"""Tests for the time-based extensions of `data/trades_buffer.py`."""
import time

from data.trades_buffer import Trade, TradesBuffer


def _mk(ts, side, price=100.0, size=0.1):
    return Trade(timestamp=ts, price=price, size=size, side=side,
                 volume_usd=price * size)


def test_get_buy_sell_volume_basic():
    now = time.time()
    buf = TradesBuffer()
    buf.add(_mk(now - 1, "B", price=100, size=2))     # 200 buy
    buf.add(_mk(now - 1, "A", price=100, size=1))     # 100 sell
    buf.add(_mk(now - 100, "B", price=100, size=10))  # outside 5s window
    buy, sell = buf.get_buy_sell_volume(5.0)
    assert buy == 200.0
    assert sell == 100.0


def test_get_trade_imbalance():
    now = time.time()
    buf = TradesBuffer()
    buf.add(_mk(now, "B", size=3))
    buf.add(_mk(now, "A", size=1))
    imb = buf.get_trade_imbalance(10.0)
    assert imb is not None
    # (300 - 100) / 400 = 0.5
    assert abs(imb - 0.5) < 1e-9


def test_imbalance_none_when_empty():
    buf = TradesBuffer()
    assert buf.get_trade_imbalance(5.0) is None


def test_prune_old_drops_only_old_trades():
    now = time.time()
    buf = TradesBuffer(max_age_seconds=10.0)
    buf.add(_mk(now - 100, "B"))
    buf.add(_mk(now - 100, "A"))
    buf.add(_mk(now, "B"))
    dropped = buf.prune_old(now=now)
    assert dropped == 2
    assert len(buf) == 1


def test_trade_count_window():
    now = time.time()
    buf = TradesBuffer()
    for i in range(5):
        buf.add(_mk(now - i, "B"))
    assert buf.get_trade_count(2.5) == 3   # i ∈ {0,1,2}
    assert buf.get_trade_count(100) == 5


def test_existing_api_preserved():
    """add / get_recent / get_vwap / __len__ must still work."""
    now = time.time()
    buf = TradesBuffer()
    buf.add(_mk(now, "B", price=100, size=1))
    buf.add(_mk(now, "A", price=110, size=2))
    assert len(buf) == 2
    assert len(buf.get_recent(60)) == 2
    vwap = buf.get_vwap(60)
    # (100 * 100 + 110 * 220) / (100 + 220) — volume_usd weighted
    expected = (100 * 100 + 110 * 220) / (100 + 220)
    assert abs(vwap - expected) < 1e-9
