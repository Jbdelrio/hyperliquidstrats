"""
tests/test_hfe_realistic.py — Phase 1 realistic-fill simulator tests.

Tests latency delay, order expiry, and dynamic slippage.

Run: python -m pytest tests/test_hfe_realistic.py -v
"""
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from execution.high_freq_executor import (
    HighFreqExecutor,
    PendingOrder,
    FillResult,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exec(tmp_path: Path, **overrides) -> HighFreqExecutor:
    cfg = {
        "paper_latency_ms":          150.0,
        "max_pending_seconds_taker": 5.0,
        "max_pending_seconds_maker": 30.0,
        "base_slippage_bps":         2.0,
    }
    cfg.update(overrides)
    log_path = tmp_path / "fills_test.csv"
    return HighFreqExecutor(
        paper=True,
        trade_log=str(log_path),
        on_fill_cb=None,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# 1. Latency delay — TAKER order does not fill before latency elapses
# ---------------------------------------------------------------------------

def test_taker_latency_delays_fill(tmp_path):
    ex = _make_exec(tmp_path, paper_latency_ms=500.0)
    pair_id = ex.place_quotes("BTC", buy_price=100.0, sell_price=200.0,
                              size_units=1.0, notional_usd=100.0,
                              order_type="TAKER_SIM")
    assert pair_id, "place_quotes should return a pair_id"

    # Right after placement the BUY should not fill even when ask <= price
    fills = ex.check_fills("BTC", best_bid=99.0, best_ask=100.0)
    assert len(fills) == 0, "TAKER fill must wait for latency"

    # Force-advance the placed_at by latency + slack so fills go through
    for order in ex._pending.values():
        order.placed_at -= 1.0   # 1s back in time

    fills = ex.check_fills("BTC", best_bid=99.0, best_ask=100.0)
    assert len(fills) == 1, "TAKER fill should fire after latency"


def test_maker_has_no_latency(tmp_path):
    """MAKER orders should fill immediately, no latency penalty."""
    ex = _make_exec(tmp_path, paper_latency_ms=10_000.0)
    pair_id = ex.place_quotes("BTC", buy_price=100.0, sell_price=200.0,
                              size_units=1.0, notional_usd=100.0,
                              order_type="MAKER_SIM")
    assert pair_id

    fills = ex.check_fills("BTC", best_bid=99.0, best_ask=100.0)
    assert len(fills) == 1, "MAKER fill should bypass latency"


# ---------------------------------------------------------------------------
# 2. Order expiry
# ---------------------------------------------------------------------------

def test_expire_stale_orders(tmp_path):
    ex = _make_exec(tmp_path, max_pending_seconds_taker=0.1)
    pair_id = ex.place_quotes("ETH", buy_price=1000.0, sell_price=2000.0,
                              size_units=0.1, notional_usd=100.0,
                              order_type="TAKER_SIM")
    assert pair_id

    # Immediately — no expiries
    expired = ex.expire_stale_orders(time.time())
    assert len(expired) == 0
    assert len(ex.pending_orders) == 2

    # After 0.5s — both siblings expire
    time.sleep(0.15)
    expired = ex.expire_stale_orders(time.time())
    # Both BUY and SELL siblings of the pair expire together
    assert len(expired) == 2, f"Expected 2 expired orders, got {len(expired)}"
    assert all(o.expired for o in expired)
    assert len(ex.pending_orders) == 0


def test_maker_expires_later_than_taker(tmp_path):
    """MAKER order survives longer than TAKER under same age."""
    ex = _make_exec(
        tmp_path,
        max_pending_seconds_taker=0.1,
        max_pending_seconds_maker=10.0,
    )
    ex.place_quotes("BTC", 100.0, 200.0, 1.0, 100.0, order_type="TAKER_SIM")
    ex.place_quotes("ETH", 1000.0, 2000.0, 0.1, 100.0, order_type="MAKER_SIM")
    time.sleep(0.15)

    expired = ex.expire_stale_orders(time.time())
    # Only BTC pair (TAKER) should expire
    syms = {o.symbol for o in expired}
    assert syms == {"BTC"}, f"Only TAKER (BTC) should have expired, got {syms}"
    # ETH MAKER survives
    assert any(o.symbol == "ETH" for o in ex.pending_orders)


# ---------------------------------------------------------------------------
# 3. Dynamic slippage
# ---------------------------------------------------------------------------

def test_taker_buy_slippage_above_ask(tmp_path):
    """TAKER BUY fills above the ask (worse for the buyer)."""
    ex = _make_exec(tmp_path, paper_latency_ms=0.0, base_slippage_bps=5.0)
    pair_id = ex.place_quotes("BTC", buy_price=100.0, sell_price=200.0,
                              size_units=1.0, notional_usd=100.0,
                              order_type="TAKER_SIM")
    assert pair_id

    # Wide-ish spread to make slippage noticeable
    fills = ex.check_fills("BTC", best_bid=99.5, best_ask=100.0)
    assert len(fills) == 1
    f = fills[0]
    assert f.side == "BUY"
    assert f.price > 100.0, "BUY fill should be above ask after slippage"
    assert f.slippage_bps > 5.0   # base + spread/2 + size_adj


def test_taker_sell_slippage_below_bid(tmp_path):
    """TAKER SELL fills below the bid (worse for the seller)."""
    ex = _make_exec(tmp_path, paper_latency_ms=0.0, base_slippage_bps=5.0)
    pair_id = ex.place_quotes("BTC", buy_price=50.0, sell_price=100.0,
                              size_units=1.0, notional_usd=100.0,
                              order_type="TAKER_SIM")
    assert pair_id

    fills = ex.check_fills("BTC", best_bid=100.0, best_ask=100.5)
    assert len(fills) == 1
    f = fills[0]
    assert f.side == "SELL"
    assert f.price < 100.0, "SELL fill should be below bid after slippage"
    assert f.slippage_bps > 5.0


def test_maker_zero_slippage(tmp_path):
    """MAKER fills use exact limit price — 0 bps slippage."""
    ex = _make_exec(tmp_path)
    ex.place_quotes("BTC", 100.0, 200.0, 1.0, 100.0, order_type="MAKER_SIM")
    fills = ex.check_fills("BTC", best_bid=99.0, best_ask=100.0)
    assert len(fills) == 1
    assert fills[0].slippage_bps == 0.0
    # MAKER BUY fills at min(order_price, best_ask) = 100.0 exactly
    assert fills[0].price == 100.0


# ---------------------------------------------------------------------------
# 4. Slippage scales with notional
# ---------------------------------------------------------------------------

def test_slippage_scales_with_notional(tmp_path):
    """Bigger orders incur more slippage."""
    ex1 = _make_exec(tmp_path / "a", paper_latency_ms=0.0)
    ex2 = _make_exec(tmp_path / "b", paper_latency_ms=0.0)

    ex1.place_quotes("BTC", 100.0, 200.0, 0.1, 1_000.0, order_type="TAKER_SIM")
    ex2.place_quotes("BTC", 100.0, 200.0, 5.0, 50_000.0, order_type="TAKER_SIM")

    f1 = ex1.check_fills("BTC", 99.5, 100.0)[0]
    f2 = ex2.check_fills("BTC", 99.5, 100.0)[0]

    assert f2.slippage_bps > f1.slippage_bps, (
        f"Larger notional must have more slippage: "
        f"$1k={f1.slippage_bps:.2f}bps vs $50k={f2.slippage_bps:.2f}bps"
    )
