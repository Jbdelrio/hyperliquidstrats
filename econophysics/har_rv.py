"""
har_rv.py — HAR-RV (Heterogeneous AutoRegressive Realized Volatility).
Ref: Corsi (2009), adapted to HF.

Predicts 15-minute-ahead volatility using 3 horizons:
  d  = 5-minute RV  (daily component in HF)
  w  = 30-minute RV (weekly component)
  m  = 120-minute RV (monthly component)

Coefficients loaded from config/har_coefs.json or use built-in defaults.
Recalibrate monthly via calibrate_har_rv.py.
"""
import json
import logging
import numpy as np
from collections import deque
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default coefficients (typical crypto HF; replace after first calibration)
_DEFAULT_COEFS = {
    "c":   -0.50,
    "b_d":  0.40,
    "b_w":  0.35,
    "b_m":  0.20,
}


class HARRealizedVolatility:
    """
    HAR-RV predictor.  Feed 1-minute log-returns via update().
    Call predict_vol() to get σ_pred and get_vol_ratio() for the sizing multiplier.
    """

    def __init__(self, coefs_path: str = "config/har_coefs.json",
                 normal_vol_window: int = 1440):
        self._returns: deque = deque(maxlen=2000)
        self._recent_rv: deque = deque(maxlen=normal_vol_window)
        self.coefs = self._load(coefs_path)

    # ------------------------------------------------------------------

    def _load(self, path: str) -> dict:
        try:
            with open(path) as f:
                coefs = json.load(f)
            log.info("HAR coefs loaded from %s (R²=%.3f)", path,
                     coefs.get("r2", float("nan")))
            return coefs
        except FileNotFoundError:
            log.warning("HAR coefs not found at %s — using defaults. "
                        "Run calibrate_har_rv.py after 7 days of data.", path)
            return dict(_DEFAULT_COEFS)

    # ------------------------------------------------------------------

    def update(self, return_1m: float) -> None:
        """Feed one 1-minute log-return (called from on_minute_close)."""
        self._returns.append(return_1m)
        if len(self._returns) >= 5:
            rv5 = float(np.sqrt(np.mean(np.array(list(self._returns)[-5:]) ** 2)))
            self._recent_rv.append(rv5)

    def predict_vol(self) -> Optional[float]:
        """Predict next-15-min RV. Returns None if warming up (<120 returns)."""
        if len(self._returns) < 120:
            return None

        rets = np.array(self._returns)
        eps = 1e-12

        rv5   = float(np.sqrt(np.sum(rets[-5:]   ** 2)))
        rv30  = float(np.sqrt(np.sum(rets[-30:]  ** 2)))
        rv120 = float(np.sqrt(np.sum(rets[-120:] ** 2)))

        log_pred = (
            self.coefs["c"]
            + self.coefs["b_d"] * np.log(rv5   + eps)
            + self.coefs["b_w"] * np.log(rv30  + eps)
            + self.coefs["b_m"] * np.log(rv120 + eps)
        )
        return float(np.exp(log_pred))

    def get_vol_ratio(self) -> Optional[float]:
        """σ_pred / σ_normal.  >1 = elevated vol, <1 = calm."""
        pred = self.predict_vol()
        if pred is None or len(self._recent_rv) < 100:
            return None
        normal = float(np.median(self._recent_rv))
        if normal <= 1e-12:
            return 1.0
        return pred / normal

    def get_size_multiplier(self) -> float:
        ratio = self.get_vol_ratio()
        if ratio is None:
            return 1.0
        if ratio > 2.5:
            return 0.2
        if ratio > 1.5:
            return 0.5
        if ratio < 0.5:
            return 1.3
        return 1.0


# ---------------------------------------------------------------------------
# Calibration helper (called by calibrate_har_rv.py)
# ---------------------------------------------------------------------------

def calibrate_har_coefs(returns_1m: np.ndarray,
                         save_path: str = "config/har_coefs.json") -> dict:
    """
    OLS calibration of HAR coefficients.
    Needs ≥1000 1-minute returns.
    """
    from sklearn.linear_model import LinearRegression

    rets = np.asarray(returns_1m, dtype=float)
    if len(rets) < 1000:
        raise ValueError(f"Need ≥1000 returns, got {len(rets)}")

    X, y = [], []
    eps = 1e-12
    for t in range(120, len(rets) - 15):
        rv5   = np.sqrt(np.sum(rets[t-5:t]   ** 2))
        rv30  = np.sqrt(np.sum(rets[t-30:t]  ** 2))
        rv120 = np.sqrt(np.sum(rets[t-120:t] ** 2))
        rv15f = np.sqrt(np.sum(rets[t:t+15]  ** 2))
        if min(rv5, rv30, rv120, rv15f) <= 0:
            continue
        X.append([np.log(rv5 + eps), np.log(rv30 + eps), np.log(rv120 + eps)])
        y.append(np.log(rv15f + eps))

    if len(X) < 200:
        raise ValueError("Too few valid observations for HAR calibration")

    X_arr, y_arr = np.array(X), np.array(y)
    reg = LinearRegression().fit(X_arr, y_arr)

    coefs = {
        "c":   float(reg.intercept_),
        "b_d": float(reg.coef_[0]),
        "b_w": float(reg.coef_[1]),
        "b_m": float(reg.coef_[2]),
        "r2":  float(reg.score(X_arr, y_arr)),
        "n_obs": int(len(y)),
    }
    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    with open(save_path, "w") as f:
        json.dump(coefs, f, indent=2)
    log.info("HAR calibration done: R²=%.3f n=%d → %s", coefs["r2"], coefs["n_obs"], save_path)
    return coefs
