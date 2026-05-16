"""Phase 8 — tp/stop fill mode = market_after_touch + fees-from-config."""
import time

import pytest

from execution.high_freq_executor import HighFreqExecutor, OpenPosition


def _make_executor(realistic: bool, fees_block=None):
    cfg = {
        "paper_latency_ms": 100,
        "base_slippage_bps": 2.0,
    }
    if realistic:
        cfg["tp_fill_mode"] = "market_after_touch"
        cfg["stop_fill_mode"] = "market_after_touch"
    if fees_block is not None:
        cfg["fees"] = fees_block
    return HighFreqExecutor(paper=True, trade_log="/tmp/_t.csv",
                            on_fill_cb=None, config=cfg,
                            orders_log="/tmp/_o.csv")


def _add_long_position(ex, *, entry=100.0, notional=100.0,
                       tp=101.0, stop=99.5, max_hold_ts=0.0):
    pos = OpenPosition(
        pos_id="x", symbol="BTC", side="BUY", entry_price=entry,
        size_units=notional / entry,
        notional_usd=notional, entry_ts=time.time(), order_type="TAKER_SIM",
        tp_price=tp, stop_price=stop, max_hold_ts=max_hold_ts,
    )
    ex._positions[pos.pos_id] = pos
    return pos


def test_legacy_exit_fills_at_tp_exact():
    ex = _make_executor(realistic=False)
    _add_long_position(ex, tp=101.0)
    # mid touches tp ; best_bid is 100.95 → legacy uses tp exact for TP.
    closes = ex.check_exits("BTC", mid=101.05, best_bid=100.95, best_ask=101.10)
    assert len(closes) == 1
    pos, exit_price, reason = closes[0]
    assert reason == "take_profit"
    assert exit_price == 101.0  # exact TP


def test_realistic_long_tp_uses_bid_minus_slippage():
    ex = _make_executor(realistic=True)
    _add_long_position(ex, tp=101.0)
    closes = ex.check_exits("BTC", mid=101.05, best_bid=100.95, best_ask=101.10)
    pos, exit_price, reason = closes[0]
    assert reason == "take_profit"
    # bid * (1 - 2bps) = 100.95 * 0.9998 = ~100.93
    assert exit_price < 100.95
    assert exit_price > 100.90


def test_realistic_short_tp_uses_ask_plus_slippage():
    ex = _make_executor(realistic=True)
    pos = OpenPosition(
        pos_id="y", symbol="BTC", side="SELL", entry_price=101.0,
        size_units=100.0 / 101.0, notional_usd=100.0,
        entry_ts=time.time(), order_type="TAKER_SIM",
        tp_price=100.0, stop_price=101.5, max_hold_ts=0.0,
    )
    ex._positions[pos.pos_id] = pos
    closes = ex.check_exits("BTC", mid=99.95, best_bid=99.93, best_ask=100.05)
    p, exit_price, reason = closes[0]
    assert reason == "take_profit"
    assert exit_price > 100.05   # ask plus slippage


def test_fees_from_config_taker():
    ex = _make_executor(realistic=True, fees_block={
        "maker_bps": 1.0, "taker_bps": 5.0,
    })
    assert ex._fees_from_config is True
    assert ex._taker_fee_bps == 5.0
    assert ex._maker_fee_bps == 1.0


def test_realistic_stop_loss_long_uses_bid_minus_slippage():
    ex = _make_executor(realistic=True)
    _add_long_position(ex, stop=99.5)
    closes = ex.check_exits("BTC", mid=99.4, best_bid=99.4, best_ask=99.45)
    pos, exit_price, reason = closes[0]
    assert reason == "stop_loss"
    # exit_price should be less than best_bid because of slippage
    assert exit_price < 99.4
