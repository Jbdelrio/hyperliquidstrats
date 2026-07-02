"""Alpha layer: map the signature vector x_t to an expected edge alpha_t (§10).

Separation of concerns: this file only *predicts* convergence/divergence. It does
not size or execute — that is the optimizer's job (§11).

Modes:
    zscore : alpha_t = -gain * z_t                       (no fitting)
    ridge  : alpha_t = K x_t, K = argmin ||y - XK||^2 + lambda||K||^2  (train only)
    elasticnet : ridge + L1 (via sklearn if available, else ridge fallback)

Targets:
    neg_spread_change : y = -(s_{t+h} - s_t)   (edge if the spread reverts)
    hedged_pnl        : y = provided realised hedged forward pnl

The estimator is fit on the TRAIN slice only. IC / R^2 are reported out of sample.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict
import numpy as np

from .config import AlphaConfig


@dataclass
class AlphaModel:
    cfg: AlphaConfig
    K: Optional[np.ndarray] = None       # (m,) linear map for ridge/elasticnet
    fitted: bool = False

    # ---- targets ---------------------------------------------------------
    @staticmethod
    def make_target_neg_spread_change(spread: np.ndarray, horizon_bars: int) -> np.ndarray:
        """y_t = -(s_{t+h} - s_t). Rows without a full horizon ahead are NaN
        (so they are *excluded* from fitting — no look-ahead leakage)."""
        s = np.asarray(spread, float)
        y = np.full(len(s), np.nan)
        h = int(horizon_bars)
        if h < 1:
            h = 1
        y[:-h] = -(s[h:] - s[:-h])
        return y

    # ---- fit / predict ---------------------------------------------------
    def fit(self, X: np.ndarray, y: np.ndarray, train_mask: np.ndarray):
        """Fit K on train rows with a finite target only."""
        if self.cfg.method == "zscore":
            self.fitted = True
            return self
        m = train_mask & np.isfinite(y) & np.all(np.isfinite(X), axis=1)
        Xt, yt = X[m], y[m]
        if len(yt) < X.shape[1] + 2:
            # not enough data: degrade to zero map (predict 0) rather than overfit
            self.K = np.zeros(X.shape[1]); self.fitted = True
            return self
        if self.cfg.method == "elasticnet":
            self.K = _fit_elasticnet(Xt, yt, self.cfg.ridge_penalty, self.cfg.l1_ratio)
        else:
            self.K = _fit_ridge(Xt, yt, self.cfg.ridge_penalty)
        self.fitted = True
        return self

    def predict(self, X: np.ndarray, zscore: Optional[np.ndarray] = None) -> np.ndarray:
        if self.cfg.method == "zscore":
            if zscore is None:
                raise ValueError("zscore mode needs the z series")
            a = -self.cfg.zscore_gain * np.nan_to_num(zscore, nan=0.0)
        else:
            if not self.fitted or self.K is None:
                raise RuntimeError("model not fitted")
            a = np.nan_to_num(X, nan=0.0) @ self.K
        # confidence filter: kill tiny signals
        a = np.where(np.abs(a) < self.cfg.min_confidence, 0.0, a)
        return a


def _fit_ridge(X: np.ndarray, y: np.ndarray, lam: float) -> np.ndarray:
    """K = (X'X + lam I)^-1 X'y via solve (never an explicit inverse)."""
    m = X.shape[1]
    A = X.T @ X + lam * np.eye(m)
    b = X.T @ y
    try:
        return np.linalg.solve(A, b)
    except np.linalg.LinAlgError:
        return np.linalg.lstsq(X, y, rcond=None)[0]


def _fit_elasticnet(X: np.ndarray, y: np.ndarray, lam: float, l1_ratio: float) -> np.ndarray:
    try:
        from sklearn.linear_model import ElasticNet
        alpha = max(lam / max(1, len(y)), 1e-6)
        en = ElasticNet(alpha=alpha, l1_ratio=l1_ratio, fit_intercept=False, max_iter=5000)
        en.fit(X, y)
        return en.coef_
    except Exception:
        return _fit_ridge(X, y, lam)


# --------------------------------------------------------------------------- #
#  Out-of-sample quality metrics (§10)
# --------------------------------------------------------------------------- #
def alpha_metrics(pred: np.ndarray, target: np.ndarray, mask: np.ndarray) -> Dict:
    m = mask & np.isfinite(pred) & np.isfinite(target)
    p, t = pred[m], target[m]
    if len(p) < 10 or np.std(p) == 0 or np.std(t) == 0:
        return {"n": int(len(p)), "ic": np.nan, "rank_ic": np.nan, "r2": np.nan, "mse": np.nan}
    ic = float(np.corrcoef(p, t)[0, 1])
    rank_ic = float(np.corrcoef(_rank(p), _rank(t))[0, 1])
    ss_res = np.sum((t - p) ** 2)
    ss_tot = np.sum((t - np.mean(t)) ** 2)
    r2 = float(1 - ss_res / ss_tot) if ss_tot > 0 else np.nan
    return {"n": int(len(p)), "ic": ic, "rank_ic": rank_ic, "r2": r2,
            "mse": float(np.mean((t - p) ** 2))}


def decile_performance(pred: np.ndarray, target: np.ndarray, mask: np.ndarray, q: int = 10):
    m = mask & np.isfinite(pred) & np.isfinite(target)
    p, t = pred[m], target[m]
    if len(p) < q * 2:
        return []
    edges = np.quantile(p, np.linspace(0, 1, q + 1))
    out = []
    for i in range(q):
        lo, hi = edges[i], edges[i + 1]
        sel = (p >= lo) & (p <= hi) if i == q - 1 else (p >= lo) & (p < hi)
        out.append(float(np.mean(t[sel])) if sel.any() else np.nan)
    return out


def _rank(a):
    order = np.argsort(np.argsort(a))
    return order.astype(float)
