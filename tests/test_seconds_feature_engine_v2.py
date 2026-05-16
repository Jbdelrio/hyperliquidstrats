"""Phase 3 additions: OFI / liquidity / toxicity / aliases."""
import math
import time
from dataclasses import dataclass
from typing import Optional

from data.seconds_feature_engine import SecondsFeatureEngine


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


def _book(bid=100.0, ask=100.05, bid_sz=2.0, ask_sz=2.0, levels=10):
    bids = [(bid - i * 0.01, bid_sz) for i in range(levels)]
    asks = [(ask + i * 0.01, ask_sz) for i in range(levels)]
    return _Book(bids, asks)


def test_ofi_positive_when_buy_dominates():
    eng = SecondsFeatureEngine(["BTC"], config={"min_warmup_seconds": 0})
    ts = time.time()
    eng.update_from_book("BTC", _book(), ts)
    eng.update_from_trade("BTC", _Trade(ts, 100, 1.0, "B", 100.0), ts)
    eng.update_from_trade("BTC", _Trade(ts, 100, 0.2, "A", 20.0), ts)
    f = eng.get_features("BTC")
    assert f["ofi_30s"] > 0
    assert "trade_volume_30s" in f and f["trade_volume_30s"] > 0


def test_ofi_negative_when_sell_dominates():
    eng = SecondsFeatureEngine(["BTC"], config={"min_warmup_seconds": 0})
    ts = time.time()
    eng.update_from_book("BTC", _book(), ts)
    eng.update_from_trade("BTC", _Trade(ts, 100, 1.0, "A", 100.0), ts)
    eng.update_from_trade("BTC", _Trade(ts, 100, 0.2, "B", 20.0), ts)
    f = eng.get_features("BTC")
    assert f["ofi_30s"] < 0


def test_aliases_present():
    eng = SecondsFeatureEngine(["BTC"], config={"min_warmup_seconds": 0})
    ts = time.time()
    eng.update_from_book("BTC", _book(), ts)
    f = eng.get_features("BTC")
    for key in ("depth_imbalance_5", "depth_imbalance_10",
                "mid_return_30s", "trade_volume_60s",
                "micro_momentum_30s"):
        assert key in f


def test_liquidity_and_toxicity_bounded():
    eng = SecondsFeatureEngine(["BTC"], config={"min_warmup_seconds": 0})
    ts = time.time()
    eng.update_from_book("BTC", _book(bid_sz=10, ask_sz=10), ts)
    eng.update_from_trade("BTC", _Trade(ts, 100, 1, "B", 100.0), ts)
    f = eng.get_features("BTC")
    assert 0.0 <= f["liquidity_score"] <= 1.0
    assert 0.0 <= f["toxicity_score"] <= 1.0


def test_book_age_and_trade_age_present():
    eng = SecondsFeatureEngine(["BTC"], config={"min_warmup_seconds": 0})
    eng.update_from_book("BTC", _book(), time.time())
    f = eng.get_features("BTC")
    assert "book_age_s" in f
    assert "trade_age_s" in f
