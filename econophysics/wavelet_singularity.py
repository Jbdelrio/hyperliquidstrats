"""
wavelet_singularity.py — CWT-based price singularity detector.
Ref: Cours Garcin (2020), section "Risque structurel multiéchelle".

A singularity = point where large CWT coefficients appear simultaneously
across multiple scales.  Detected here via max-module method.
When a singularity is detected, all quotes for that symbol are cancelled
and a 30-second cooldown is set.

Dependency: PyWavelets (pip install PyWavelets)
"""
import logging
import time
import numpy as np
from collections import deque
from typing import Optional

try:
    import pywt
    _PYWT_AVAILABLE = True
except ImportError:
    _PYWT_AVAILABLE = False

log = logging.getLogger(__name__)


class WaveletSingularityDetector:
    """
    Detects imminent large price moves via CWT maxima across scales.

    Algorithm per book update:
      1. CWT on log-returns of last `window` prices (scales 2..32)
      2. Extract max |coeff| at the current edge (last 5 obs)
      3. Compare to rolling baseline (median per scale over history)
      4. If ≥ min_alerted_scales exceed threshold × baseline → ALERT
      5. Cooldown: quotes suspended for cooldown_s seconds
    """

    def __init__(self,
                 window: int = 200,
                 scales: Optional[np.ndarray] = None,
                 wavelet: str = "morl",
                 alert_threshold: float = 3.0,
                 min_alerted_scales: int = 3,
                 baseline_window: int = 500,
                 cooldown_s: float = 30.0,
                 check_every_n: int = 10):

        if not _PYWT_AVAILABLE:
            log.warning("PyWavelets not installed. WaveletSingularityDetector disabled.")

        self.window = window
        self.wavelet = wavelet
        self.alert_threshold = alert_threshold
        self.min_alerted_scales = min_alerted_scales
        self.cooldown_s = cooldown_s
        self.check_every_n = check_every_n   # only run CWT every N updates (perf)

        self.scales = scales if scales is not None else np.arange(2, 33, dtype=float)

        self._prices: deque = deque(maxlen=window)
        self._baseline_history: deque = deque(maxlen=baseline_window)
        self._baseline: Optional[np.ndarray] = None

        self._cooldown_until: float = 0.0
        self._counter: int = 0
        self._last_alert_scales: int = 0

    def update(self, price: float, timestamp: float) -> bool:
        """
        Feed a new price. Returns True if an alert is active.
        """
        self._prices.append(price)
        self._counter += 1

        if timestamp < self._cooldown_until:
            return True

        if not _PYWT_AVAILABLE:
            return False

        if len(self._prices) < self.window:
            return False

        # Only run expensive CWT every check_every_n updates
        if self._counter % self.check_every_n != 0:
            return False

        alert = self._check_singularity(timestamp)
        return alert

    def _check_singularity(self, timestamp: float) -> bool:
        prices_arr = np.array(self._prices)
        log_rets = np.diff(np.log(np.maximum(prices_arr, 1e-12)))

        if len(log_rets) < 50:
            return False

        try:
            coeffs, _ = pywt.cwt(log_rets, self.scales, self.wavelet)
        except Exception as e:
            log.debug("CWT failed: %s", e)
            return False

        abs_coeffs = np.abs(coeffs)

        # Current edge: max over last 5 positions
        edge_modules = abs_coeffs[:, -5:].max(axis=1)

        # Update baseline with median across current window
        self._baseline_history.append(abs_coeffs.mean(axis=1))
        if len(self._baseline_history) < 50:
            return False

        self._baseline = np.median(np.array(self._baseline_history), axis=0)
        safe_baseline = self._baseline + 1e-10

        ratios = edge_modules / safe_baseline
        alerted = int(np.sum(ratios > self.alert_threshold))
        self._last_alert_scales = alerted

        if alerted >= self.min_alerted_scales:
            self._cooldown_until = timestamp + self.cooldown_s
            log.debug("Wavelet alert: %d scales > %.1f× baseline", alerted, self.alert_threshold)
            return True

        return False

    def is_alert_active(self, timestamp: float) -> bool:
        return timestamp < self._cooldown_until

    def get_size_multiplier(self, timestamp: float) -> float:
        return 0.0 if self.is_alert_active(timestamp) else 1.0

    @property
    def last_alert_scales(self) -> int:
        return self._last_alert_scales
