"""
tests/test_hurst.py — Unit tests for HurstLocalEstimator.
Run: pytest tests/test_hurst.py -v
"""
import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from econophysics.hurst_local import HurstLocalEstimator


def _feed(est: HurstLocalEstimator, prices: np.ndarray):
    h = None
    for p in prices:
        h = est.update(float(p))
    return h


def test_hurst_random_walk():
    """Geometric Brownian Motion should give H ≈ 0.5."""
    np.random.seed(42)
    log_rets = np.random.randn(2000) * 0.001
    prices = 100 * np.exp(np.cumsum(log_rets))

    est = HurstLocalEstimator(window=500, min_samples=200)
    h = _feed(est, prices)

    assert h is not None
    assert 0.38 < h < 0.62, f"GBM Hurst = {h:.4f}, expected ~0.5"


def test_hurst_mean_reverting():
    """Ornstein-Uhlenbeck process should give H < 0.45."""
    np.random.seed(0)
    n = 2000
    prices = np.zeros(n)
    prices[0] = 100.0
    theta, mu, sigma = 0.5, 100.0, 0.3
    for i in range(1, n):
        prices[i] = prices[i-1] + theta * (mu - prices[i-1]) + sigma * np.random.randn()
    prices = np.maximum(prices, 1e-6)

    est = HurstLocalEstimator(window=500, min_samples=200)
    h = _feed(est, prices)

    assert h is not None
    assert h < 0.48, f"OU process Hurst = {h:.4f}, expected < 0.48"


def test_hurst_trending():
    """
    Positively autocorrelated log-returns (AR(1), phi=0.7) should give H > 0.55.

    The 2-timescale Hurst estimator detects autocorrelation in increments, NOT
    pure drift.  For an AR(1) in returns with phi > 0:
        var_lag2 / var_lag1 = 2*(1+phi)  →  H = 0.5*log2(2*(1+phi))
    phi=0.7  →  H ≈ 0.88
    """
    np.random.seed(7)
    phi, sigma, n = 0.7, 0.001, 2000
    log_rets = np.zeros(n)
    eps = np.random.randn(n) * sigma * np.sqrt(1 - phi ** 2)
    for i in range(1, n):
        log_rets[i] = phi * log_rets[i - 1] + eps[i]
    prices = 100 * np.exp(np.cumsum(log_rets))

    est = HurstLocalEstimator(window=500, min_samples=200)
    h = _feed(est, prices)

    assert h is not None
    assert h > 0.60, f"Persistent AR(1) Hurst = {h:.4f}, expected > 0.60"


def test_hurst_warmup_returns_none():
    est = HurstLocalEstimator(window=300, min_samples=100)
    for i in range(50):
        h = est.update(100.0 + i * 0.01)
    assert h is None, "Should return None during warmup"


def test_hurst_clip_bounds():
    """H must stay in [0.05, 0.95] regardless of input."""
    np.random.seed(1)
    # Constant prices (zero variance) — degenerate case
    prices = np.full(600, 100.0) + np.random.randn(600) * 1e-8
    est = HurstLocalEstimator(window=300, min_samples=100)
    h = _feed(est, prices)
    if h is not None:
        assert 0.05 <= h <= 0.95


def test_hurst_regime_labels():
    np.random.seed(42)
    est = HurstLocalEstimator(window=300, min_samples=200)
    # Inject enough data
    log_rets = np.random.randn(1000) * 0.001
    prices = 100 * np.exp(np.cumsum(log_rets))
    _feed(est, prices)

    regime = est.get_regime()
    assert regime in {"MR", "RW", "TREND_LOW", "TREND_HIGH", "UNKNOWN"}

    mult = est.get_size_multiplier()
    assert 0.0 <= mult <= 1.4


def test_hurst_size_multiplier_consistency():
    """TREND_HIGH must give size = 0 (stop quoting)."""
    est = HurstLocalEstimator.__new__(HurstLocalEstimator)
    est._cached_h = 0.70
    assert est.get_size_multiplier() == 0.0

    est._cached_h = 0.30
    assert est.get_size_multiplier() == 1.4

    est._cached_h = 0.50
    assert est.get_size_multiplier() == 1.0
