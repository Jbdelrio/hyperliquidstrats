"""Tests for `data/seconds_feature_engine.py`."""
import math
import time
from dataclasses import dataclass
from typing import Optional

from data.seconds_feature_engine import SecondsFeatureEngine


# Minimal book mock (compatible with OrderBook duck-typing)
@dataclass
class _Book:
    bids: list
    asks: list

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None


@dataclass
class _Trade:
    timestamp: float
    price: float
    size: float
    side: str
    volume_usd: float


def _mk_book(bid=100.0, ask=100.1, bid_sz=2.0, ask_sz=2.0, levels=10):
    bids = [(bid - i * 0.01, bid_sz) for i in range(levels)]
    asks = [(ask + i * 0.01, ask_sz) for i in range(levels)]
    return _Book(bids=bids, asks=asks)


def test_obi_basic():
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 0})
    ts = time.time()
    # Bid heavy
    book = _mk_book(bid_sz=3.0, ask_sz=1.0)
    eng.update_from_book("BTC", book, ts)
    f = eng.get_features("BTC")
    # at depth 5 : bid = 5 * 3 = 15 ; ask = 5 * 1 = 5 → OBI = (15-5)/20 = 0.5
    assert abs(f["obi_5"] - 0.5) < 1e-9
    assert abs(f["obi_1"] - 0.5) < 1e-9    # same ratio at level 1


def test_microprice_calculation():
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 0})
    ts = time.time()
    bid = 100.0
    ask = 100.10
    book = _Book(
        bids=[(bid, 3.0)],
        asks=[(ask, 1.0)],
    )
    eng.update_from_book("BTC", book, ts)
    f = eng.get_features("BTC")
    mid = (bid + ask) / 2
    # microprice = (ask * q_bid + bid * q_ask) / (q_bid + q_ask)
    expected = (ask * 3.0 + bid * 1.0) / (3.0 + 1.0)
    assert abs(f["microprice"] - expected) < 1e-9
    assert f["microprice_pressure"] == (expected - mid) / mid


def test_trade_imbalance_window():
    eng = SecondsFeatureEngine(["ETH"], config={"min_data_seconds": 0})
    ts = time.time()
    eng.update_from_book("ETH", _mk_book(), ts)
    eng.update_from_trade("ETH", _Trade(ts, 100, 0.5, "B", 50.0), ts)
    eng.update_from_trade("ETH", _Trade(ts, 100, 0.2, "A", 20.0), ts)
    f = eng.get_features("ETH")
    # (50-20)/70 = 0.4285...
    assert abs(f["trade_imbalance_10s"] - (30.0 / 70.0)) < 1e-9
    assert f["buy_volume_usd_10s"] == 50.0
    assert f["sell_volume_usd_10s"] == 20.0


def test_vwap_calculation():
    eng = SecondsFeatureEngine(["SOL"], config={"min_data_seconds": 0})
    ts = time.time()
    eng.update_from_book("SOL", _mk_book(), ts)
    eng.update_from_trade("SOL", _Trade(ts, 100.0, 1.0, "B", 100.0), ts)
    eng.update_from_trade("SOL", _Trade(ts, 110.0, 1.0, "A", 110.0), ts)
    f = eng.get_features("SOL")
    # vwap = (100*100 + 110*110) / (100+110)
    expected = (100 * 100 + 110 * 110) / (100 + 110)
    assert abs(f["vwap_5s"] - expected) < 1e-9


def test_returns_seconds():
    """r_5s should equal log(mid_now / mid_5s_ago)."""
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 0})
    ts0 = 1_000_000.0
    # Two ticks 5 seconds apart with mid 100 → 105.
    eng.update_from_book("BTC", _Book([(100.0, 1)], [(100.0, 1)]), ts0)
    eng.update_from_book("BTC", _Book([(105.0, 1)], [(105.0, 1)]), ts0 + 5.0)
    f = eng.get_features("BTC")
    # r_5s is computed against latest mid → mid 5s ago.
    expected = math.log(105.0 / 100.0)
    assert abs(f["r_5s"] - expected) < 1e-6


def test_realized_volatility_finite():
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 0})
    ts0 = 1_000_000.0
    px = 100.0
    for i in range(30):
        px *= 1.0001 if i % 2 == 0 else 0.9999
        eng.update_from_book("BTC", _Book([(px, 1)], [(px, 1)]), ts0 + i)
    f = eng.get_features("BTC")
    assert math.isfinite(f["rv_30s"])
    assert f["rv_30s"] > 0


def test_enough_data_false_when_history_too_short():
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 60})
    ts = time.time()
    eng.update_from_book("BTC", _mk_book(), ts)
    f = eng.get_features("BTC")
    assert f["enough_data"] is False


def test_book_stale_after_inactivity():
    eng = SecondsFeatureEngine(["BTC"], config={
        "min_data_seconds": 0, "stale_book_s": 0.001,
    })
    eng.update_from_book("BTC", _mk_book(), time.time() - 5)
    f = eng.get_features("BTC")
    assert f["book_stale"] is True


def test_book_flow_divergence_and_alignment():
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 0})
    ts = time.time()
    # OBI positive at level 5 — bid heavy book
    book = _mk_book(bid_sz=3.0, ask_sz=1.0)
    eng.update_from_book("BTC", book, ts)
    # Trades : net SELL → ti negative
    eng.update_from_trade("BTC", _Trade(ts, 100, 0.1, "A", 100.0), ts)
    eng.update_from_trade("BTC", _Trade(ts, 100, 0.05, "B", 50.0), ts)
    f = eng.get_features("BTC")
    assert f["book_flow_alignment"] in (-1.0, 1.0)
    assert math.isfinite(f["book_flow_divergence"])
    # ti10 ≈ (50-100)/150 = -1/3 ; obi_5 = 0.5 → div = -1/3 - 0.5 = -0.833
    assert f["book_flow_divergence"] < 0


def test_absorption_proxies_non_negative():
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 0})
    ts = time.time()
    eng.update_from_book("BTC", _mk_book(), ts)
    # Add trades — buy heavy
    eng.update_from_trade("BTC", _Trade(ts, 100, 1, "B", 100.0), ts)
    f = eng.get_features("BTC")
    assert f["absorption_buy_proxy"] >= 0
    assert f["absorption_sell_proxy"] >= 0


def test_liquidity_vacuum_nan_when_no_history():
    eng = SecondsFeatureEngine(["BTC"], config={"min_data_seconds": 0})
    eng.update_from_book("BTC", _mk_book(), time.time())
    f = eng.get_features("BTC")
    # Not enough z-score samples yet
    assert math.isnan(f["liquidity_vacuum"])
