"""Optimal-execution layer: learn the linear speed policy v_t = B x_t (§11).

The spread inventory Q_t is a scalar (a unit of spread = long 1 leg-1, short
beta_t leg-2). The trading speed is v_t = B x_t with x_t the signature vector, so
B is a row vector and theta = B^T is an m-vector.

Because Q_t = Q_0 + dt * sum_{s<t} v_s = theta^T c_t with c_t = dt * cumsum(x),
every term of the empirical objective is quadratic in theta:

    maximise  J(theta) = sum_t [ Q_t alpha_t                     (edge)
                                 - lambda v_t^2                   (exec cost)
                                 - phi sigma_t^2 Q_t^2            (inventory risk)
                                 - eta (netdollar_t Q_t)^2 ] dt   (dollar neutrality)
                         - gamma Q_T^2                            (terminal liquidation)
                         - rho ||theta||^2                        (ridge)

=> J = h^T theta - theta^T M theta,  theta* = 1/2 M^{-1} h, obtained with
``np.linalg.solve`` (never an explicit inverse). Episodes reset Q so the terminal
penalty is meaningful per holding window.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple
import numpy as np

from .config import OptimizerConfig


@dataclass
class ExecutionPolicy:
    theta: np.ndarray                 # (m,) used to trade: v_t = theta^T x_t (possibly scaled)
    theta_raw: np.ndarray             # the exact quadratic solution (unscaled)
    scale: float                      # theta = scale * theta_raw
    diagnostics: Dict


def fit_policy(X: np.ndarray, alpha: np.ndarray, sigma: np.ndarray,
               net_dollar: np.ndarray, cfg: OptimizerConfig, dt: float,
               episode_bars: int, train_mask: np.ndarray,
               scale_to_utilization: float = 0.0) -> ExecutionPolicy:
    """Solve for theta on the training rows, episode by episode.

    ``scale_to_utilization`` (0 disables): after solving, rescale theta so that the
    95th percentile of |Q_t| simulated on the training rows equals
    ``scale_to_utilization * inventory_cap``. This ONLY changes the policy's overall
    magnitude (a single positive scalar), not the learned direction/shape B/||B||.
    The exact solution is preserved in ``theta_raw`` so regularisation effects on
    ||B|| remain observable.
    """
    m = X.shape[1]
    M = cfg.execution_ridge_rho * np.eye(m)         # rho ||theta||^2
    h = np.zeros(m)
    # term accumulators for diagnostics
    contrib = {"exec_cost": 0.0, "inventory_risk": 0.0, "dollar_neutrality": 0.0}

    idx = np.where(train_mask)[0]
    if len(idx) < m + 2:
        z = np.zeros(m)
        return ExecutionPolicy(theta=z, theta_raw=z, scale=1.0,
                               diagnostics={"note": "insufficient train data",
                                            "condition_number": np.nan})
    # contiguous training runs, each split into episodes of episode_bars
    for run in _contiguous_runs(idx):
        for lo, hi in _episodes(run, episode_bars):
            Xe = np.nan_to_num(X[lo:hi], nan=0.0)
            ae = np.nan_to_num(alpha[lo:hi], nan=0.0)
            se = np.nan_to_num(sigma[lo:hi], nan=0.0)
            pe = np.nan_to_num(net_dollar[lo:hi], nan=0.0)
            n = len(Xe)
            if n < 3:
                continue
            # c_t = dt * cumsum of x over s < t  (so Q_t = theta^T c_t)
            c = dt * np.vstack([np.zeros(m), np.cumsum(Xe, axis=0)[:-1]])
            # edge (linear): + sum alpha_t Q_t dt = h^T theta
            h += dt * (ae[:, None] * c).sum(axis=0)
            # exec cost (quad): lambda sum v_t^2 dt = theta^T (lambda dt sum x x^T) theta
            Mx = cfg.execution_cost_lambda * dt * (Xe.T @ Xe)
            # inventory risk (quad): phi sum sigma^2 Q^2 dt
            w_inv = cfg.inventory_risk_phi * dt * (se ** 2)
            Minv = (c * w_inv[:, None]).T @ c
            # dollar neutrality (quad): eta sum (netdollar Q)^2 dt
            w_dol = cfg.dollar_neutrality_eta * dt * (pe ** 2)
            Mdol = (c * w_dol[:, None]).T @ c
            # terminal liquidation: gamma Q_T^2 with Q_T = theta^T c_last(+last v)
            cT = c[-1] + dt * Xe[-1]
            Mterm = cfg.terminal_penalty_gamma * np.outer(cT, cT)
            M += Mx + Minv + Mdol + Mterm
            contrib["exec_cost"] += float(np.trace(Mx))
            contrib["inventory_risk"] += float(np.trace(Minv))
            contrib["dollar_neutrality"] += float(np.trace(Mdol))

    # theta* = 1/2 M^{-1} h  via solve(2M, h)
    A = 2.0 * M
    try:
        theta = np.linalg.solve(A, h)
    except np.linalg.LinAlgError:
        theta = np.linalg.lstsq(A, h, rcond=None)[0]

    diag = _matrix_diagnostics(M)
    diag.update(contrib)
    diag["theta_raw_norm"] = float(np.linalg.norm(theta))
    diag["edge_grad_norm"] = float(np.linalg.norm(h))

    # optional magnitude normalization (single positive scalar; keeps direction)
    scale = 1.0
    if scale_to_utilization and scale_to_utilization > 0 and np.linalg.norm(theta) > 0:
        Q, _ = simulate_inventory(X, theta, cfg, dt, episode_bars, no_trade_band=0.0)
        Qtr = np.abs(Q[train_mask])
        p95 = np.percentile(Qtr[np.isfinite(Qtr)], 95) if np.isfinite(Qtr).any() else 0.0
        if p95 > 1e-12:
            scale = float(scale_to_utilization * cfg.inventory_cap / p95)
    diag["policy_scale"] = scale
    diag["theta_norm"] = float(np.linalg.norm(theta) * scale)
    return ExecutionPolicy(theta=theta * scale, theta_raw=theta, scale=scale, diagnostics=diag)


def simulate_inventory(X: np.ndarray, theta: np.ndarray, cfg: OptimizerConfig,
                       dt: float, episode_bars: int, tradeable: Optional[np.ndarray] = None,
                       no_trade_band: float = 0.0) -> Tuple[np.ndarray, np.ndarray]:
    """Roll the policy forward -> (target inventory Q_t, speed v_t).

    - v_t = clip(theta^T x_t, +/- v_max)
    - Q evolves and is capped to +/- inventory_cap
    - a no-trade band freezes Q if the desired change is below the band
    - each episode ends flat (terminal liquidation glide handled by the objective;
      here we hard-reset Q at episode boundaries and force the last step to unwind)
    """
    n, m = X.shape
    Q = np.zeros(n)
    v = np.zeros(n)
    q = 0.0
    for t in range(n):
        episode_end = ((t + 1) % episode_bars == 0) or (t == n - 1)
        if tradeable is not None and not tradeable[t]:
            desired_v = -q / dt                      # glide flat when not tradeable
        else:
            raw = float(theta @ np.nan_to_num(X[t], nan=0.0))
            desired_v = np.clip(raw, -cfg.maximum_velocity, cfg.maximum_velocity)
        q_next = q + desired_v * dt
        q_next = float(np.clip(q_next, -cfg.inventory_cap, cfg.inventory_cap))
        if episode_end:
            q_next = 0.0                             # terminal liquidation
        # no-trade band on the *change* in inventory
        if abs(q_next - q) < no_trade_band and not episode_end:
            q_next = q
        v[t] = (q_next - q) / dt
        q = q_next
        Q[t] = q
    return Q, v


# --------------------------------------------------------------------------- #
#  helpers
# --------------------------------------------------------------------------- #
def _contiguous_runs(idx: np.ndarray):
    if len(idx) == 0:
        return []
    splits = np.where(np.diff(idx) != 1)[0] + 1
    return [r for r in np.split(idx, splits) if len(r) > 0]


def _episodes(run: np.ndarray, episode_bars: int):
    lo = run[0]; hi = run[-1] + 1
    out = []
    a = lo
    while a < hi:
        b = min(a + episode_bars, hi)
        out.append((a, b))
        a = b
    return out


def _matrix_diagnostics(M: np.ndarray) -> Dict:
    try:
        w = np.linalg.eigvalsh((M + M.T) / 2)
    except np.linalg.LinAlgError:
        w = np.array([np.nan])
    w = w[np.isfinite(w)]
    if len(w) == 0:
        return {"condition_number": np.nan, "effective_rank": np.nan,
                "min_eigenvalue": np.nan, "max_eigenvalue": np.nan, "stability_warning": True}
    wpos = w[w > 0]
    cond = float(w.max() / wpos.min()) if len(wpos) else np.inf
    # effective rank via singular-value entropy
    s = np.abs(w); s = s[s > 0]
    p = s / s.sum() if s.sum() > 0 else s
    eff_rank = float(np.exp(-np.sum(p * np.log(p)))) if len(p) else np.nan
    return {"condition_number": cond, "effective_rank": eff_rank,
            "min_eigenvalue": float(w.min()), "max_eigenvalue": float(w.max()),
            "stability_warning": bool(cond > 1e8 or w.min() < 0)}
