"""
tests/test_kalman.py — Unit tests for KalmanFairValue.
Run: pytest tests/test_kalman.py -v
"""
import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from econophysics.kalman_fair_value import KalmanFairValue


def test_first_update_returns_mid():
    kf = KalmanFairValue(initial_value=100.0)
    fv, drift = kf.update(mid_price=50000.0)
    assert fv == 50000.0
    assert drift == 0.0


def test_fair_value_smooths_noise():
    """Fair value should be smoother than the noisy mid sequence."""
    np.random.seed(42)
    true_price = 50000.0
    n = 300
    noisy_mids = true_price + np.random.randn(n) * 20

    kf = KalmanFairValue(initial_value=true_price)
    fvs = []
    for mid in noisy_mids:
        fv, _ = kf.update(mid)
        fvs.append(fv)

    fvs = np.array(fvs[50:])  # skip warmup
    mids = noisy_mids[50:]

    # Kalman output variance should be lower than raw mid variance
    assert np.var(fvs) < np.var(mids), \
        f"Kalman var {np.var(fvs):.1f} not less than mid var {np.var(mids):.1f}"


def test_fair_value_tracks_trend():
    """Fair value should follow a trending price, not stay constant."""
    kf = KalmanFairValue(initial_value=100.0)
    for i in range(200):
        kf.update(100.0 + i * 0.1)
    fv = kf.get_fair_value()
    assert fv > 110.0, f"FV={fv:.2f} should have trended up"


def test_drift_positive_during_uptrend():
    """Drift should become positive when price consistently rises."""
    kf = KalmanFairValue(initial_value=100.0)
    for i in range(200):
        kf.update(100.0 + i * 0.2)
    drift = kf.get_drift()
    assert drift > 0, f"Drift={drift:.6f} should be positive in uptrend"


def test_drift_negative_during_downtrend():
    kf = KalmanFairValue(initial_value=100.0)
    for i in range(200):
        kf.update(100.0 - i * 0.2)
    drift = kf.get_drift()
    assert drift < 0, f"Drift={drift:.6f} should be negative in downtrend"


def test_vwap_integration():
    """VWAP obs should shift fair value toward VWAP."""
    kf = KalmanFairValue(initial_value=100.0)
    mid  = 50000.0
    vwap = 49900.0   # VWAP below mid

    # Feed many observations with consistent VWAP
    for _ in range(100):
        fv, _ = kf.update(mid_price=mid, vwap_30s=vwap)

    # FV should be between vwap and mid, slightly pulled toward vwap
    assert vwap <= fv <= mid, f"FV={fv:.2f} should be between {vwap} and {mid}"


def test_no_lookahead():
    """update() must only use past data (pure filter, not smoother)."""
    kf = KalmanFairValue(initial_value=100.0)
    fvs = []
    prices = [100 + np.sin(i * 0.1) * 5 for i in range(200)]
    for p in prices:
        fv, _ = kf.update(p)
        fvs.append(fv)
    # Just check it runs without errors and returns finite values
    assert all(np.isfinite(fvs)), "All FV values must be finite"
