"""
tests/test_bouchaud.py — Unit tests for BouchaudImpactModel.
Run: pytest tests/test_bouchaud.py -v
"""
import time
import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from econophysics.bouchaud_impact import BouchaudImpactModel


def test_empty_pressure_is_zero():
    model = BouchaudImpactModel()
    assert model.get_pressure(time.time()) == 0.0


def test_all_buys_gives_positive_pressure():
    model = BouchaudImpactModel(decay_s=60)
    now = time.time()
    bid, ask = 49990.0, 50010.0
    for i in range(20):
        model.add_trade(now - i, price=50010.0, volume_usd=100,
                        best_bid=bid, best_ask=ask, side="B")
    pressure = model.get_pressure(now)
    assert pressure > 0.5, f"All-buy pressure={pressure:.3f} should be > 0.5"


def test_all_sells_gives_negative_pressure():
    model = BouchaudImpactModel(decay_s=60)
    now = time.time()
    bid, ask = 49990.0, 50010.0
    for i in range(20):
        model.add_trade(now - i, price=49990.0, volume_usd=100,
                        best_bid=bid, best_ask=ask, side="A")
    pressure = model.get_pressure(now)
    assert pressure < -0.5, f"All-sell pressure={pressure:.3f} should be < -0.5"


def test_balanced_flow_near_zero():
    model = BouchaudImpactModel(decay_s=60)
    now = time.time()
    bid, ask = 49990.0, 50010.0
    for i in range(30):
        side = "B" if i % 2 == 0 else "A"
        price = ask if side == "B" else bid
        model.add_trade(now - i, price=price, volume_usd=100,
                        best_bid=bid, best_ask=ask, side=side)
    pressure = model.get_pressure(now)
    assert abs(pressure) < 0.3, f"Balanced pressure={pressure:.3f} should be near 0"


def test_decay_reduces_pressure():
    model = BouchaudImpactModel(decay_s=5)
    now = time.time()
    bid, ask = 49990.0, 50010.0
    # Old trades
    for i in range(20):
        model.add_trade(now - 100 - i, price=50010.0, volume_usd=100,
                        best_bid=bid, best_ask=ask, side="B")
    pressure_old = abs(model.get_pressure(now))

    # Recent trades
    model2 = BouchaudImpactModel(decay_s=5)
    for i in range(20):
        model2.add_trade(now - i, price=50010.0, volume_usd=100,
                         best_bid=bid, best_ask=ask, side="B")
    pressure_new = abs(model2.get_pressure(now))

    assert pressure_new > pressure_old, \
        "Recent trades should contribute more pressure than old trades"


def test_pressure_bounded():
    model = BouchaudImpactModel(decay_s=30)
    now = time.time()
    bid, ask = 49990.0, 50010.0
    for i in range(100):
        model.add_trade(now - i * 0.1, price=50010.0, volume_usd=1e9,
                        best_bid=bid, best_ask=ask, side="B")
    p = model.get_pressure(now)
    assert -1.0 <= p <= 1.0, f"Pressure {p} must be in [-1, 1]"


def test_quote_skew_direction():
    model = BouchaudImpactModel(decay_s=30)
    now = time.time()
    bid, ask = 49990.0, 50010.0
    spread = ask - bid

    for _ in range(20):
        model.add_trade(now, price=ask, volume_usd=100,
                        best_bid=bid, best_ask=ask, side="B")
    skew = model.get_quote_skew(now, spread)
    assert skew > 0, "Buy pressure should produce positive (upward) quote skew"


def test_ambiguous_price_ignored():
    """Trade price between bid and ask without explicit side should be ignored."""
    model = BouchaudImpactModel()
    now = time.time()
    bid, ask = 49990.0, 50010.0
    model.add_trade(now, price=50000.0, volume_usd=100,
                    best_bid=bid, best_ask=ask, side=None)
    assert model.get_pressure(now) == 0.0
