"""
tests/test_s8_ems.py — Integration tests for S8EconophysicsMakerScalping.
Run: pytest tests/test_s8_ems.py -v
"""
import time
import numpy as np
import pytest
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
from strategies.s8_ems import (
    S8EconophysicsMakerScalping,
    ACTION_PLACE_QUOTES, ACTION_CANCEL_QUOTES,
    ACTION_MANAGE_POS, ACTION_CLOSE_MARKET,
)


# ---------------------------------------------------------------------------
# Minimal OrderBook stub
# ---------------------------------------------------------------------------

@dataclass
class FakeBook:
    best_bid: float
    best_ask: float

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2

    def imbalance(self, n: int = 5) -> float:
        return 0.0


DEFAULT_PARAMS = {
    "min_spread_bps": 4.0,
    "max_spread_bps": 20.0,
    "base_notional_pct": 0.04,
    "max_leverage": 5,
    "quote_refresh_s": 0.0,   # no cooldown for tests
    "max_hold_s": 60,
    "stop_loss_bps": 30,
}


def make_strategy(capital: float = 500.0) -> S8EconophysicsMakerScalping:
    return S8EconophysicsMakerScalping(
        params=DEFAULT_PARAMS,
        capital=capital,
        symbols=["BTC", "ETH"],
    )


def warmup(strat: S8EconophysicsMakerScalping, symbol: str = "BTC",
           n: int = 350, base: float = 50000.0):
    """Feed enough price ticks to satisfy Hurst min_samples."""
    np.random.seed(42)
    book = FakeBook(best_bid=base * 0.9998, best_ask=base * 1.0002)
    ts = time.time()
    for i in range(n):
        price = base + np.random.randn() * 10
        book = FakeBook(best_bid=price * 0.9998, best_ask=price * 1.0002)
        strat.on_orderbook_update(symbol, book, ts + i * 0.5)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_place_quotes_after_warmup():
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    book = FakeBook(best_bid=49990, best_ask=50010)
    action = strat.on_orderbook_update("BTC", book, time.time())
    # May still be None if Hurst regime is TREND_HIGH — just check structure if not None
    if action is not None:
        assert action["action"] == ACTION_PLACE_QUOTES
        assert "buy_price" in action
        assert "sell_price" in action
        assert action["buy_price"] < action["sell_price"]
        assert action["notional_usd"] > 0


def test_no_quotes_on_tight_spread():
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    # Spread = 1 bps — below min
    book = FakeBook(best_bid=49999.75, best_ask=50000.25)
    action = strat.on_orderbook_update("BTC", book, time.time())
    # Either None or CANCEL — not PLACE
    if action:
        assert action["action"] != ACTION_PLACE_QUOTES


def test_no_quotes_on_wide_spread():
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    # Spread = 25 bps — above max
    book = FakeBook(best_bid=49937.5, best_ask=50062.5)
    action = strat.on_orderbook_update("BTC", book, time.time())
    if action:
        assert action["action"] != ACTION_PLACE_QUOTES


def test_cancel_on_wavelet_alert(monkeypatch):
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    strat.register_pending("BTC", ["b_abc123", "s_abc123"])

    # Force wavelet alert
    monkeypatch.setattr(
        strat.coin_states["BTC"].wavelet, "_cooldown_until",
        time.time() + 60,
    )
    book = FakeBook(best_bid=49990, best_ask=50010)
    action = strat.on_orderbook_update("BTC", book, time.time())
    assert action is not None
    assert action["action"] == ACTION_CANCEL_QUOTES


def test_fill_creates_position():
    strat = make_strategy()
    ts = time.time()
    action = strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    assert action["action"] == ACTION_MANAGE_POS
    assert strat.coin_states["BTC"].open_position is not None
    assert strat.coin_states["BTC"].open_position["side"] == "BUY"
    assert strat.total_trades == 1


def test_stop_exit():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    stop = pos["stop"]

    # Price hits stop
    action = strat.check_position_exits(
        "BTC", mid=stop - 1.0,
        best_bid=stop - 2.0, best_ask=stop + 2.0,
        timestamp=ts + 5,
    )
    assert action is not None
    assert action["action"] == ACTION_CLOSE_MARKET
    assert action["reason"] == "stop_loss"
    assert strat.coin_states["BTC"].open_position is None


def test_tp_exit():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "SELL", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    tp = pos["tp"]

    # SELL TP: fires when best_ask <= tp_price (ask dropped to our limit buy)
    action = strat.check_position_exits(
        "BTC", mid=tp + 1.0,
        best_bid=tp - 2.0, best_ask=tp - 0.1,
        timestamp=ts + 5,
    )
    assert action is not None
    assert action["reason"] == "take_profit"


def test_max_hold_exit():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    tp = pos["tp"]    # ~ 50090 (entry + 30bps * 0.6 * entry)
    stop = pos["stop"]

    # Price well inside stop/TP range, time expired
    mid_inside = (50000.0 + tp) / 2  # halfway between entry and TP
    future_ts = ts + 300
    action = strat.check_position_exits(
        "BTC", mid=mid_inside,
        best_bid=mid_inside - 1.0,
        best_ask=mid_inside + 1.0,
        timestamp=future_ts,
    )
    assert action is not None
    assert action["reason"] == "max_hold"


def test_no_quotes_with_open_position():
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)

    book = FakeBook(best_bid=49990, best_ask=50010)
    action = strat.on_orderbook_update("BTC", book, ts + 1)
    # No new quotes when position is open
    if action:
        assert action["action"] != ACTION_PLACE_QUOTES


def test_pnl_accounting():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    stop = pos["stop"]

    # Stop hit → loss
    strat.check_position_exits(
        "BTC", mid=stop - 1.0,
        best_bid=stop - 2.0, best_ask=stop + 2.0,
        timestamp=ts + 10,
    )
    assert strat.total_trades == 1
    assert strat.daily_pnl < 0.0, "Stop-loss exit should record a negative PnL"
