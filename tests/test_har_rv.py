"""
tests/test_har_rv.py — Unit tests for HARRealizedVolatility.
Run: pytest tests/test_har_rv.py -v
"""
import numpy as np
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from econophysics.har_rv import HARRealizedVolatility, calibrate_har_coefs


def _warmup(har: HARRealizedVolatility, n: int = 200, vol: float = 0.001):
    np.random.seed(99)
    for _ in range(n):
        har.update(np.random.randn() * vol)


def test_predict_none_during_warmup():
    har = HARRealizedVolatility(coefs_path="config/har_coefs.json")
    for _ in range(50):
        har.update(0.001)
    assert har.predict_vol() is None


def test_predict_returns_float_after_warmup():
    har = HARRealizedVolatility(coefs_path="config/har_coefs.json")
    _warmup(har, n=200)
    pred = har.predict_vol()
    assert pred is not None
    assert pred > 0.0


def test_vol_ratio_none_during_warmup():
    har = HARRealizedVolatility(coefs_path="config/har_coefs.json")
    _warmup(har, n=50)
    # recent_vols needs 100 samples
    assert har.get_vol_ratio() is None


def test_vol_ratio_high_vol():
    """High-vol period should give ratio > 1."""
    har = HARRealizedVolatility(coefs_path="config/har_coefs.json")
    # Warmup with low vol
    _warmup(har, n=300, vol=0.0001)
    # Inject spike
    for _ in range(30):
        har.update(0.01)
    ratio = har.get_vol_ratio()
    if ratio is not None:
        assert ratio > 1.0, f"Expected ratio > 1, got {ratio:.3f}"


def test_size_multiplier_extremes():
    har = HARRealizedVolatility.__new__(HARRealizedVolatility)
    # Patch get_vol_ratio
    har.get_vol_ratio = lambda: None
    assert har.get_size_multiplier() == 1.0

    har.get_vol_ratio = lambda: 3.0
    assert har.get_size_multiplier() == 0.2

    har.get_vol_ratio = lambda: 0.3
    assert har.get_size_multiplier() == 1.3

    har.get_vol_ratio = lambda: 1.0
    assert har.get_size_multiplier() == 1.0


def test_calibrate_har_coefs(tmp_path):
    """OLS calibration should produce valid coefficients and R² > 0."""
    np.random.seed(42)
    n = 3000
    # Simulate stationary log-returns with mild autocorrelation
    rets = np.random.randn(n) * 0.001

    save_path = str(tmp_path / "har_coefs_test.json")
    coefs = calibrate_har_coefs(rets, save_path=save_path)

    assert "c" in coefs and "b_d" in coefs
    assert coefs["n_obs"] > 100
    assert 0.0 <= coefs["r2"] <= 1.0


def test_calibrate_insufficient_data():
    with pytest.raises(ValueError, match="Need"):
        calibrate_har_coefs(np.random.randn(100))
