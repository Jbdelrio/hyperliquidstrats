"""Spread construction and statistical diagnostics (§5, §6).

All hedge-ratio estimators are **causal**: the value at row t uses information up
to and including t only (no smoothing / no future data). Methods:

    ratio  : s_t = logP1 - beta0 * logP2                (fixed beta)
    ols    : rolling OLS   logP1 = a_t + b_t logP2       -> s_t = residual
    ridge  : rolling ridge (stabilised b_t)
    kalman : state-space (a_t, b_t) random walk, filtered
    factor : logP_alt - sum_k b_k logP_factor_k          (market-neutral residual)

Diagnostics return the individual metrics *and* a synthetic quality score, plus a
boolean ``tradeable`` mask that disables entries on regime problems (§6).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple, List
import numpy as np
import pandas as pd

from .config import SpreadConfig


@dataclass
class SpreadResult:
    spread: np.ndarray            # s_t
    zscore: np.ndarray           # z_t (rolling)
    alpha: np.ndarray            # a_t
    beta: np.ndarray             # b_t (leg-2 hedge ratio; factor: first factor)
    betas_factor: Optional[np.ndarray] = None   # (T, k) for method=factor


# --------------------------------------------------------------------------- #
#  Hedge-ratio estimators
# --------------------------------------------------------------------------- #
def _rolling_ols(y: np.ndarray, x: np.ndarray, win: int, ridge: float = 0.0):
    """Causal rolling regression y = a + b x over a trailing window.

    beta = Cov(x,y)/(Var(x)+ridge), alpha = mean(y) - beta*mean(x). min_periods
    lets early rows use the available prefix (still causal).
    """
    ys = pd.Series(y); xs = pd.Series(x)
    mp = max(5, win // 10)
    mx = xs.rolling(win, min_periods=mp).mean()
    my = ys.rolling(win, min_periods=mp).mean()
    mxy = (xs * ys).rolling(win, min_periods=mp).mean()
    mxx = (xs * xs).rolling(win, min_periods=mp).mean()
    cov = mxy - mx * my
    var = (mxx - mx * mx) + ridge
    beta = (cov / var).to_numpy()
    alpha = (my - pd.Series(beta, index=ys.index) * mx).to_numpy()
    return alpha, beta


def _kalman_hedge(y: np.ndarray, x: np.ndarray, cfg: SpreadConfig):
    """2-state Kalman filter for (alpha_t, beta_t) with observation y=a+b*x.

    Random-walk state, Q = process var * I, R = observation var. Filtered (causal)
    estimates only.
    """
    n = len(y)
    a = np.empty(n); b = np.empty(n)
    state = np.array([cfg.kalman_alpha_init, cfg.kalman_beta_init], float)
    P = np.eye(2) * cfg.kalman_init_cov
    Q = np.eye(2) * cfg.kalman_process_variance
    R = cfg.kalman_observation_variance
    for t in range(n):
        # predict (random walk): state unchanged, covariance grows
        P = P + Q
        H = np.array([1.0, x[t]])
        yhat = H @ state
        S = H @ P @ H + R
        K = (P @ H) / S
        state = state + K * (y[t] - yhat)
        P = P - np.outer(K, H) @ P
        a[t] = state[0]; b[t] = state[1]
    return a, b


def _rolling_factor(y: np.ndarray, factors: np.ndarray, win: int, ridge: float,
                    stride: int) -> Tuple[np.ndarray, np.ndarray]:
    """Causal rolling multi-factor ridge: y = b0 + sum_k b_k factor_k.

    Refit every ``stride`` rows and hold coefficients in between (realistic and
    keeps this O(T/stride)). Returns (residual s_t, betas (T,k))."""
    T, k = factors.shape
    X = np.column_stack([np.ones(T), factors])
    resid = np.full(T, np.nan)
    betas = np.full((T, k), np.nan)
    coef = None
    lamI = ridge * np.eye(k + 1); lamI[0, 0] = 0.0
    mp = max(20, win // 10)
    for t in range(T):
        if t >= mp and (coef is None or t % max(1, stride) == 0):
            lo = max(0, t - win + 1)
            Xw = X[lo:t + 1]; yw = y[lo:t + 1]
            A = Xw.T @ Xw + lamI
            try:
                coef = np.linalg.solve(A, Xw.T @ yw)
            except np.linalg.LinAlgError:
                coef = np.linalg.lstsq(Xw, yw, rcond=None)[0]
        if coef is not None:
            resid[t] = y[t] - X[t] @ coef
            betas[t] = coef[1:]
    return resid, betas


# --------------------------------------------------------------------------- #
#  Public entry
# --------------------------------------------------------------------------- #
def build_spread(log_p1: np.ndarray, log_p2: np.ndarray, cfg: SpreadConfig,
                 bars_per_window: int, log_factors: Optional[np.ndarray] = None,
                 decision_stride: int = 1) -> SpreadResult:
    y = np.asarray(log_p1, float)
    x = np.asarray(log_p2, float)
    betas_factor = None

    if cfg.method == "ratio":
        beta = np.full_like(y, cfg.static_beta)
        alpha = np.zeros_like(y)
        spread = y - beta * x
    elif cfg.method in ("ols", "ridge"):
        ridge = cfg.ridge_lambda if cfg.method == "ridge" else 0.0
        alpha, beta = _rolling_ols(y, x, bars_per_window, ridge)
        spread = y - alpha - beta * x
    elif cfg.method == "kalman":
        alpha, beta = _kalman_hedge(y, x, cfg)
        spread = y - alpha - beta * x
    elif cfg.method == "factor":
        if log_factors is None:
            raise ValueError("method=factor requires log_factors (T,k)")
        spread, betas_factor = _rolling_factor(y, log_factors, bars_per_window,
                                               cfg.ridge_lambda, decision_stride)
        beta = betas_factor[:, 0] if betas_factor.shape[1] else np.zeros_like(y)
        alpha = np.zeros_like(y)
    else:                              # pragma: no cover - guarded by config.validate
        raise ValueError(f"unknown method {cfg.method}")

    # clamp hedge ratio to configured bounds (numerical safety)
    beta = np.clip(beta, cfg.hedge_ratio_min, cfg.hedge_ratio_max)
    z = _rolling_z(spread, bars_per_window)
    return SpreadResult(spread=spread, zscore=z, alpha=alpha, beta=beta,
                        betas_factor=betas_factor)


def _rolling_z(s: np.ndarray, win: int) -> np.ndarray:
    ss = pd.Series(s)
    mp = max(5, win // 10)
    mu = ss.rolling(win, min_periods=mp).mean()
    sd = ss.rolling(win, min_periods=mp).std(ddof=0)
    return ((ss - mu) / sd.replace(0, np.nan)).to_numpy()


# --------------------------------------------------------------------------- #
#  Statistical diagnostics
# --------------------------------------------------------------------------- #
def half_life(spread: np.ndarray) -> float:
    """OU half-life in bars from Δs_t = θ s_{t-1} + c + e (θ<0 -> reversion)."""
    s = np.asarray(spread, float)
    s = s[np.isfinite(s)]
    if len(s) < 20:
        return np.nan
    ds = np.diff(s)
    lag = s[:-1]
    X = np.column_stack([np.ones(len(lag)), lag])
    try:
        theta = np.linalg.lstsq(X, ds, rcond=None)[0][1]
    except np.linalg.LinAlgError:
        return np.nan
    if theta >= 0:
        return np.inf                # not mean-reverting
    return float(-np.log(2) / theta)


def adf_pvalue(spread: np.ndarray) -> float:
    """Augmented Dickey-Fuller p-value (arch if available, else NaN)."""
    s = np.asarray(spread, float)
    s = s[np.isfinite(s)]
    if len(s) < 30:
        return np.nan
    # cap the sample for the unit-root test (speed) — tail is representative
    if len(s) > 4000:
        s = s[-4000:]
    try:
        from arch.unitroot import ADF
        return float(ADF(s, max_lags=8).pvalue)
    except Exception:
        return np.nan


def engle_granger_pvalue(log_p1: np.ndarray, log_p2: np.ndarray) -> float:
    """Engle-Granger cointegration p-value: ADF on the static-regression residual."""
    y = np.asarray(log_p1, float); x = np.asarray(log_p2, float)
    m = np.isfinite(y) & np.isfinite(x)
    y, x = y[m], x[m]
    if len(y) < 40:
        return np.nan
    X = np.column_stack([np.ones(len(x)), x])
    try:
        b = np.linalg.lstsq(X, y, rcond=None)[0]
    except np.linalg.LinAlgError:
        return np.nan
    return adf_pvalue(y - X @ b)


def variance_ratio(spread: np.ndarray, k: int = 5) -> float:
    """VR(k): Var(k-step)/ (k Var(1-step)). <1 => mean reversion."""
    s = np.asarray(spread, float); s = s[np.isfinite(s)]
    if len(s) < 5 * k:
        return np.nan
    d1 = np.diff(s)
    dk = s[k:] - s[:-k]
    v1 = np.var(d1, ddof=1)
    if v1 == 0:
        return np.nan
    return float(np.var(dk, ddof=1) / (k * v1))


def zero_crossings(spread: np.ndarray) -> int:
    s = np.asarray(spread, float); s = s[np.isfinite(s)]
    if len(s) < 2:
        return 0
    c = s - np.nanmean(s)
    return int(np.sum(np.diff(np.sign(c)) != 0))


def hedge_stability(beta: np.ndarray) -> float:
    """std(beta)/|mean(beta)| over the finite tail (lower = more stable)."""
    b = np.asarray(beta, float); b = b[np.isfinite(b)]
    if len(b) < 10:
        return np.nan
    m = np.abs(np.mean(b))
    return float(np.std(b) / m) if m > 1e-12 else np.inf


def diagnostics(res: SpreadResult, log_p1: np.ndarray, log_p2: np.ndarray,
                cfg: SpreadConfig, tail: int = 5000) -> Dict:
    """Compute the full diagnostic dict + a synthetic quality score in [0,1]."""
    s = res.spread[-tail:]
    hl = half_life(s)
    adf = adf_pvalue(s)
    eg = engle_granger_pvalue(log_p1[-tail:], log_p2[-tail:])
    vr = variance_ratio(s)
    zc = zero_crossings(s)
    stab = hedge_stability(res.beta[-tail:])
    finite_z = res.zscore[np.isfinite(res.zscore)]
    extreme_freq = float(np.mean(np.abs(finite_z) > 2)) if len(finite_z) else np.nan

    # synthetic score (individual metrics are NOT hidden — all returned)
    def _s(cond):
        return 1.0 if cond else 0.0
    parts = [
        _s(np.isfinite(adf) and adf <= cfg.adf_pvalue_max),
        _s(np.isfinite(hl) and cfg.min_halflife_bars <= hl <= cfg.max_halflife_bars),
        _s(np.isfinite(vr) and vr < 1.0),
        _s(np.isfinite(stab) and stab <= cfg.hedge_stability_max),
        _s(np.isfinite(eg) and eg <= cfg.adf_pvalue_max),
    ]
    quality = float(np.mean(parts))
    return {
        "half_life_bars": hl, "adf_pvalue": adf, "engle_granger_pvalue": eg,
        "variance_ratio": vr, "zero_crossings": zc, "hedge_stability": stab,
        "extreme_excursion_freq": extreme_freq, "quality_score": quality,
        "beta_mean": float(np.nanmean(res.beta)), "beta_last": float(_last_finite(res.beta)),
    }


def tradeable_mask(res: SpreadResult, cfg: SpreadConfig, vol: Optional[np.ndarray] = None,
                   vol_max: Optional[float] = None) -> np.ndarray:
    """Per-bar boolean: True where new entries are allowed (§6 regime gates)."""
    T = len(res.spread)
    ok = np.ones(T, bool)
    # rolling half-life / adf are expensive per-bar; use rolling hedge stability and z gate
    z = res.zscore
    ok &= np.isfinite(z)
    ok &= np.abs(np.nan_to_num(z, nan=1e9)) <= cfg.max_zscore_abs
    # rolling hedge instability: local std/|mean| of beta over a short window
    b = pd.Series(res.beta)
    win = max(20, T // 50)
    inst = (b.rolling(win, min_periods=win // 2).std()
            / b.rolling(win, min_periods=win // 2).mean().abs().replace(0, np.nan))
    ok &= (inst.fillna(1e9).to_numpy() <= cfg.hedge_stability_max) | (~np.isfinite(inst.to_numpy()))
    if vol is not None and vol_max is not None:
        ok &= np.nan_to_num(vol, nan=0.0) <= vol_max
    return ok


def _last_finite(a: np.ndarray) -> float:
    a = np.asarray(a, float)
    fin = a[np.isfinite(a)]
    return float(fin[-1]) if len(fin) else np.nan
