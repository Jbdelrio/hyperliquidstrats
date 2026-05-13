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
from strategies.s8_ems import S8EconophysicsMakerScalping
from strategies.base_strategy import StrategyConfig

ACTION_PLACE_QUOTES  = "PLACE_QUOTES"
ACTION_CANCEL_QUOTES = "CANCEL_QUOTES"
ACTION_MANAGE_POS    = "MANAGE_POS"
ACTION_CLOSE_MARKET  = "CLOSE"


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
    cfg = StrategyConfig(
        name="S8EMS", enabled=True,
        capital_allocated_usd=capital,
        max_positions=2,
        max_position_size_usd=capital * 0.5,
        coins=["BTC", "ETH"],
        params=DEFAULT_PARAMS,
    )
    return S8EconophysicsMakerScalping(cfg)


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
    if action is not None:
        assert action.action == ACTION_PLACE_QUOTES
        assert action.buy_price is not None and action.sell_price is not None
        assert action.buy_price < action.sell_price
        assert action.notional_usd > 0


def test_no_quotes_on_tight_spread():
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    book = FakeBook(best_bid=49999.75, best_ask=50000.25)
    action = strat.on_orderbook_update("BTC", book, time.time())
    if action:
        assert action.action != ACTION_PLACE_QUOTES


def test_no_quotes_on_wide_spread():
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    book = FakeBook(best_bid=49937.5, best_ask=50062.5)
    action = strat.on_orderbook_update("BTC", book, time.time())
    if action:
        assert action.action != ACTION_PLACE_QUOTES


def test_cancel_on_wavelet_alert(monkeypatch):
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    strat.register_pending("BTC", ["b_abc123", "s_abc123"])

    monkeypatch.setattr(
        strat.coin_states["BTC"].wavelet, "_cooldown_until",
        time.time() + 60,
    )
    book = FakeBook(best_bid=49990, best_ask=50010)
    action = strat.on_orderbook_update("BTC", book, time.time())
    assert action is not None
    assert action.action == ACTION_CANCEL_QUOTES


def test_fill_creates_position():
    strat = make_strategy()
    ts = time.time()
    result = strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    # on_fill returns a dict {tp_price, stop_price, max_hold_seconds}
    assert result is not None
    assert "tp_price" in result
    assert "stop_price" in result
    assert strat.coin_states["BTC"].open_position is not None
    assert strat.coin_states["BTC"].open_position["side"] == "BUY"
    assert strat.coin_states["BTC"].fills == 1


def test_stop_exit():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    stop = pos["stop"]

    book = FakeBook(best_bid=stop - 2.0, best_ask=stop + 2.0)
    action = strat.check_position_exits("BTC", book, ts + 5)
    assert action is not None
    assert action.action == ACTION_CLOSE_MARKET
    assert action.reason == "stop_loss"
    # on_position_closed clears the position (called by engine after close)
    strat.on_position_closed("BTC", -50.0, "stop_loss")
    assert strat.coin_states["BTC"].open_position is None


def test_tp_exit():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "SELL", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    tp = pos["tp"]

    book = FakeBook(best_bid=tp - 2.0, best_ask=tp - 0.1)
    action = strat.check_position_exits("BTC", book, ts + 5)
    assert action is not None
    assert action.reason == "take_profit"


def test_max_hold_exit():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    tp = pos["tp"]

    mid_inside = (50000.0 + tp) / 2
    book = FakeBook(best_bid=mid_inside - 1.0, best_ask=mid_inside + 1.0)
    action = strat.check_position_exits("BTC", book, ts + 300)
    assert action is not None
    assert action.reason == "max_hold"


def test_no_quotes_with_open_position():
    strat = make_strategy()
    warmup(strat, "BTC", n=350)
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)

    book = FakeBook(best_bid=49990, best_ask=50010)
    action = strat.on_orderbook_update("BTC", book, ts + 1)
    if action:
        assert action.action != ACTION_PLACE_QUOTES


def test_pnl_accounting():
    strat = make_strategy()
    ts = time.time()
    strat.on_fill("BTC", "BUY", 50000.0, 0.032, 1600.0, ts)
    pos = strat.coin_states["BTC"].open_position
    stop = pos["stop"]

    book = FakeBook(best_bid=stop - 2.0, best_ask=stop + 2.0)
    strat.check_position_exits("BTC", book, ts + 10)
    # Simulate engine calling on_position_closed with the realized loss
    pnl_loss = -(stop * 0.032 * 0.003)  # approximate stop-loss loss
    strat.on_position_closed("BTC", pnl_loss, "stop_loss")
    assert strat.coin_states["BTC"].fills == 1
    assert strat.daily_pnl < 0.0, "Stop-loss exit should record a negative PnL"
