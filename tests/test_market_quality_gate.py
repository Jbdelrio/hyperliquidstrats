"""Tests for risk/market_quality_gate.py."""
import math

import pytest

from risk.market_quality_gate import MarketQualityGate


def _good_features(**overrides):
    f = dict(
        symbol="BTC",
        mid=50000.0,
        best_bid=49999.0, best_ask=50001.0,
        spread_bps=0.4,
        book_age_s=0.5, trade_age_s=2.0,
        ofi_30s=0.05, depth_imbalance_10=0.05,
        trade_volume_30s=100_000.0,
        rv_60s=0.001,           # 10 bps
        toxicity_score=0.3,
        liquidity_score=0.8,
        enough_data=True,
    )
    f.update(overrides)
    return f


def _good_health(symbol="BTC", **overrides):
    h = {
        "queue_drops": 0,
        "crossed_book_count": 0,
        "per_symbol": {
            symbol: {"p95_latency_ms": 150.0},
        },
    }
    h.update(overrides)
    return h


def test_block_no_book():
    g = MarketQualityGate({})
    ok, reason, _ = g.evaluate("BTC", "long", {}, book=None, health=None)
    assert not ok
    assert reason == "no_book"


def test_block_book_stale():
    g = MarketQualityGate({"max_book_age_s": 2.0})
    f = _good_features(book_age_s=10.0)
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason == "book_stale"


def test_block_spread_too_wide():
    g = MarketQualityGate({})
    f = _good_features(spread_bps=20.0)  # > BTC cap 5
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason.startswith("spread_too_wide")


def test_block_latency_high():
    g = MarketQualityGate({"max_latency_p95_ms": 500})
    f = _good_features()
    health = {"queue_drops": 0, "crossed_book_count": 0,
              "per_symbol": {"BTC": {"p95_latency_ms": 1200.0}}}
    ok, reason, _ = g.evaluate("BTC", "long", f, health=health)
    assert not ok
    assert reason.startswith("latency_p95")


def test_block_low_volume():
    g = MarketQualityGate({})
    f = _good_features(trade_volume_30s=100.0)  # < BTC cap 25000
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason.startswith("low_volume")


def test_block_realized_vol_high():
    g = MarketQualityGate({"max_realized_vol_60s_bps": 30})
    f = _good_features(rv_60s=0.01)  # 100 bps
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason.startswith("realized_vol_high")


def test_block_toxicity_high():
    g = MarketQualityGate({"max_toxicity_score": 0.5})
    f = _good_features(toxicity_score=0.9)
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason.startswith("toxicity_high")


def test_block_liquidity_low():
    g = MarketQualityGate({"min_liquidity_score": 0.5})
    f = _good_features(liquidity_score=0.2)
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason.startswith("liquidity_low")


def test_block_ofi_against_long():
    g = MarketQualityGate({"ofi_block_threshold": 0.20})
    f = _good_features(ofi_30s=-0.5)
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason.startswith("ofi_against_long")


def test_block_ofi_against_short():
    g = MarketQualityGate({"ofi_block_threshold": 0.20})
    f = _good_features(ofi_30s=0.5)
    ok, reason, _ = g.evaluate("BTC", "short", f, health=_good_health())
    assert not ok
    assert reason.startswith("ofi_against_short")


def test_block_depth_against_long():
    g = MarketQualityGate({"depth_block_threshold": 0.20})
    f = _good_features(depth_imbalance_10=-0.5)
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason.startswith("depth_against_long")


def test_block_warmup():
    g = MarketQualityGate({})
    f = _good_features(enough_data=False)
    ok, reason, _ = g.evaluate("BTC", "long", f, health=_good_health())
    assert not ok
    assert reason == "warmup"


def test_block_queue_drops_recent():
    g = MarketQualityGate({})
    g._last_queue_drops = 0
    f = _good_features()
    health = {"queue_drops": 5, "crossed_book_count": 0,
              "per_symbol": {"BTC": {"p95_latency_ms": 150}}}
    ok, reason, _ = g.evaluate("BTC", "long", f, health=health)
    assert not ok
    assert reason == "queue_drops_recent"


def test_pass_when_all_good():
    g = MarketQualityGate({})
    f = _good_features()
    ok, reason, details = g.evaluate("BTC", "long", f, health=_good_health())
    assert ok, reason
    assert reason == "ok"
    assert details["spread_bps"] == 0.4


def test_stats_tracked():
    g = MarketQualityGate({})
    f = _good_features(spread_bps=20.0)
    g.evaluate("BTC", "long", f, health=_good_health())
    assert g.stats.total_blocked == 1
    assert g.stats.blocks_by_reason.get("spread_too_wide") == 1


def test_disabled_passes_everything():
    g = MarketQualityGate({"enabled": False})
    ok, reason, _ = g.evaluate("BTC", "long", {}, book=None, health=None)
    assert ok and reason == "disabled"
