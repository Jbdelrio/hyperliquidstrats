"""Backtest engine (§7, §11, §18, §19) — ties the whole strategy together.

Pipeline (all causal):
    load -> spread + diagnostics -> information path Z_t -> rolling signatures x_t
         -> alpha (train-fit) -> execution policy v=Bx (train-fit)
         -> inventory Q_t -> risk sizing -> two-leg fills + costs -> PnL/metrics

Operating grid: the strategy re-decides on the *decision* grid (>= market grid).
Signatures are maintained INCREMENTALLY on that grid via Chen's identity and reset
every ``signature.window_seconds`` (an "episode"), which is both efficient and the
behaviour asked for in §8. PnL is marked bar-to-bar on the decision grid.

Nothing here can place a live order (BACKTEST_ONLY, §32).
"""
from __future__ import annotations

from typing import Dict, Optional, List
import numpy as np
import pandas as pd

from .config import StrategyConfig, freq_seconds
from .data_loader import load_pair, PairData
from . import spread_model as sm
from .path_builder import build_channel_matrix, Normalizer
from .signature_features import (IncrementalSignature, signature_dimension,
                                 signature_feature_names, levy_area)
from .alpha_model import AlphaModel, alpha_metrics, decile_performance
from .execution_optimizer import fit_policy, simulate_inventory
from .costs import CostModel
from .fill_models import FillModel
from .two_leg_execution import TwoLegExecutionManager
from .risk_manager import RiskManager
from .walk_forward import single_split, make_folds
from .metrics import compute_metrics


def run_backtest(cfg: StrategyConfig, data: Optional[PairData] = None,
                 walk_forward: bool = False, max_bars: int = 60000) -> Dict:
    cfg.validate()
    if data is None:
        data = load_pair(cfg.data)
    rng = np.random.default_rng(cfg.seed)

    prep = _prepare(cfg, data, max_bars=max_bars)  # decision-grid arrays + signatures
    if walk_forward:
        return _run_walk_forward(cfg, data, prep, rng)
    # default: single chronological train/val/test split
    fold = single_split(prep["ts"], 0.6, 0.2,
                        label_horizon_seconds=cfg.alpha.horizon_seconds,
                        purge_seconds=cfg.walk_forward.purge_seconds)
    res = _run_fold(cfg, data, prep, fold.train, fold.test, rng)
    res["benchmarks"] = _benchmarks(cfg, prep, fold.test)
    res["is_demo"] = bool(data.is_demo)
    res["data_meta"] = data.meta
    res["config"] = cfg.to_dict()
    return res


# --------------------------------------------------------------------------- #
#  Preparation (shared across folds)
# --------------------------------------------------------------------------- #
def _prepare(cfg: StrategyConfig, data: PairData, max_bars: int = 60000) -> Dict:
    m_step = freq_seconds(cfg.data.market_data_frequency)
    d_step = freq_seconds(cfg.data.decision_frequency)
    k = max(1, d_step // m_step)
    # coarsening safeguard: keep the decision grid <= max_bars so any span/frequency
    # stays runnable and the GUI is responsive. The effective cadence is recorded.
    n_bars = len(range(0, data.n, k))
    coarsen = 1
    if max_bars and n_bars > max_bars:
        coarsen = int(np.ceil(n_bars / max_bars))
        k *= coarsen
        d_step *= coarsen
        data.meta = {**data.meta, "coarsened_x": coarsen,
                     "effective_decision_seconds": d_step,
                     "note_coarsen": f"grid coarsened x{coarsen} to stay <= {max_bars} bars"}
    idx = np.arange(0, data.n, k)
    ts = data.ts[idx].astype(float)
    lp1 = data.log_p1[idx]; lp2 = data.log_p2[idx]
    p1 = data.p1[idx]; p2 = data.p2[idx]

    win_bars = max(5, cfg.spread.window_seconds // d_step)
    factors = None
    if cfg.spread.method == "factor" and data.factors_log is not None:
        factors = data.factors_log[idx]
    elif cfg.spread.method == "factor" and data.btc_log is not None:
        factors = np.column_stack([data.btc_log[idx]])
    spread = sm.build_spread(lp1, lp2, cfg.spread, win_bars, factors, decision_stride=1)
    diag = sm.diagnostics(spread, lp1, lp2, cfg.spread)

    # channel series
    r1 = np.concatenate([[0.0], np.diff(lp1)])
    r2 = np.concatenate([[0.0], np.diff(lp2)])
    btc_r = (np.concatenate([[0.0], np.diff(data.btc_log[idx])])
             if data.btc_log is not None else np.zeros(len(idx)))
    vol = pd.Series(r1).rolling(win_bars, min_periods=5).std(ddof=0).fillna(0.0).to_numpy()
    ofi = data.ofi[idx] if data.ofi is not None else np.zeros(len(idx))
    funding = data.funding[idx] if data.funding is not None else np.zeros(len(idx))
    book = data.book_spread_bps[idx] if data.book_spread_bps is not None else np.zeros(len(idx))
    series = {"spread": spread.spread, "zscore": spread.zscore,
              "asset_1_return": r1, "asset_2_return": r2, "btc_return": btc_r,
              "order_flow_imbalance": ofi, "realized_volatility": vol,
              "book_spread": book, "funding": funding}

    sig_win = max(3, cfg.signature.window_seconds // d_step)
    pm = build_channel_matrix(cfg.signature, series, sig_win)
    norm = Normalizer(cfg.signature, sig_win)
    if cfg.signature.normalization == "train_fit":
        # fit deferred to fold (needs train mask); store raw and fit per fold
        norm_matrix = pm.matrix
    else:
        norm_matrix = norm.transform(pm)

    X = _incremental_signatures(norm_matrix, cfg.signature.depth, sig_win)
    feat_names = signature_feature_names(cfg.signature.channels, cfg.signature.depth)

    # Lévy areas on the last window (for the lead-lag panel)
    levy = levy_area(norm_matrix[-sig_win:]) if len(norm_matrix) >= 2 else np.zeros(
        (cfg.signature.n_channels, cfg.signature.n_channels))

    return {"ts": ts, "p1": p1, "p2": p2, "lp1": lp1, "lp2": lp2,
            "spread": spread, "diag": diag, "series": series, "d_step": d_step,
            "X": X, "feature_names": feat_names, "channel_matrix": pm, "normalizer": norm,
            "sig_win": sig_win, "win_bars": win_bars, "vol": vol, "book": book, "ofi": ofi,
            "levy": levy, "tradeable": sm.tradeable_mask(spread, cfg.spread, vol,
                                                         vol_max=None)}


def _incremental_signatures(mat: np.ndarray, depth: int, reset_window: int) -> np.ndarray:
    """Signature since the start of the current episode, reset every reset_window
    steps. Causal. For depth<=2 this is computed with a fully-vectorised closed
    form per episode (level-1 = cumulative increment, level-2 = cumulative iterated
    integral); depth 3 falls back to the exact Chen-append loop.
    """
    T, d = mat.shape
    m = signature_dimension(d, depth)
    out = np.zeros((T, m))
    if depth >= 3:                      # exact but slower fallback
        acc = IncrementalSignature(depth, d)
        for t in range(T):
            if t % reset_window == 0:
                acc = IncrementalSignature(depth, d)
            acc.append(mat[t])
            out[t] = acc.value()
        return out
    out[:, 0] = 1.0                     # level-0 constant
    for lo in range(0, T, reset_window):
        hi = min(lo + reset_window, T)
        P = mat[lo:hi]
        L = P.shape[0]
        if L < 2:
            continue
        A = P - P[0]                    # increments from episode start (L, d)
        out[lo + 1:hi, 1:1 + d] = A[1:]  # level 1 at row lo+s (s>=1) = A[s]
        if depth >= 2:
            dd = np.diff(P, axis=0)     # segment increments (L-1, d)
            # term_k = outer(A_k, d_k) + 0.5 outer(d_k, d_k)
            term = np.einsum('ki,kj->kij', A[:-1], dd) + 0.5 * np.einsum('ki,kj->kij', dd, dd)
            s2 = np.cumsum(term, axis=0).reshape(L - 1, d * d)  # value after each segment
            out[lo + 1:hi, 1 + d:1 + d + d * d] = s2
    return out


# --------------------------------------------------------------------------- #
#  One fold
# --------------------------------------------------------------------------- #
def _run_fold(cfg: StrategyConfig, data: PairData, prep: Dict,
              train: np.ndarray, test: np.ndarray, rng) -> Dict:
    X = prep["X"]; spread = prep["spread"]; d_step = prep["d_step"]
    T = len(prep["ts"])
    train_mask = np.zeros(T, bool); train_mask[train] = True

    # (re)fit train_fit normalization if configured
    if cfg.signature.normalization == "train_fit":
        norm = prep["normalizer"].fit(prep["channel_matrix"].matrix, train_mask)
        mat = norm.transform(prep["channel_matrix"])
        X = _incremental_signatures(mat, cfg.signature.depth, prep["sig_win"])

    # ---- alpha (train only) ----
    horizon_bars = max(1, cfg.alpha.horizon_seconds // d_step)
    y = AlphaModel.make_target_neg_spread_change(spread.spread, horizon_bars)
    amodel = AlphaModel(cfg.alpha).fit(X, y, train_mask)
    alpha = amodel.predict(X, zscore=spread.zscore)
    a_metrics = alpha_metrics(alpha, y, ~train_mask)          # OOS
    deciles = decile_performance(alpha, y, ~train_mask)

    # ---- execution policy v=Bx (train only) ----
    episode_bars = max(2, cfg.risk.maximum_holding_period_seconds // d_step)
    net_dollar = np.abs(1.0 - spread.beta)
    policy = fit_policy(X, alpha, prep["vol"], net_dollar, cfg.optimizer, dt=1.0,
                        episode_bars=episode_bars, train_mask=train_mask,
                        scale_to_utilization=cfg.optimizer.policy_scale_target)
    band_frac = cfg.execution.no_trade_band_usd / max(cfg.risk.maximum_position_per_leg, 1e-9)
    Qf, v = simulate_inventory(X, policy.theta, cfg.optimizer, dt=1.0,
                               episode_bars=episode_bars, tradeable=prep["tradeable"],
                               no_trade_band=band_frac)

    # ---- PnL accounting on the TEST window ----
    acct = _account(cfg, prep, Qf, spread.beta, test, rng)
    metrics = compute_metrics(acct["pnl"], acct["equity"], acct["turnover"],
                              acct["n_trades"], bar_seconds=d_step,
                              holding_times=acct["holding_runs"], fees_total=acct["fees"],
                              funding_total=acct["funding"], initial_capital=cfg.risk.initial_capital,
                              extra={"maker_fill_ratio": acct["maker_ratio"],
                                     "max_leg_imbalance": acct["max_imbalance"],
                                     "desync_incidents": acct["desync"]})
    return {
        "metrics": metrics, "spread_diagnostics": prep["diag"],
        "alpha_metrics": a_metrics, "alpha_deciles": deciles,
        "signature": {"n_channels": cfg.signature.n_channels, "depth": cfg.signature.depth,
                      "dimension": int(X.shape[1]), "feature_names": prep["feature_names"],
                      "missing_channels": prep["channel_matrix"].missing},
        "optimizer_diagnostics": policy.diagnostics,
        "levy_area": prep["levy"].tolist(),
        "policy_scale": policy.scale,
        "series": _series_for_ui(prep, alpha, v, Qf, acct, test),
        "trades": acct["trades"][:2000],
        "test_range": [float(prep["ts"][test[0]]), float(prep["ts"][test[-1]])] if len(test) else None,
    }


def _account(cfg: StrategyConfig, prep: Dict, Qf: np.ndarray, beta: np.ndarray,
             test: np.ndarray, rng) -> Dict:
    p1 = prep["p1"]; p2 = prep["p2"]; ts = prep["ts"]; book = prep["book"]
    vol = prep["vol"]; ofi = prep["ofi"]
    cost = CostModel(cfg.costs)
    fills = FillModel(cfg.execution, rng)
    twoleg = TwoLegExecutionManager(cost, fills)
    risk = RiskManager(cfg.risk)

    equity = cfg.risk.initial_capital
    pnl, eq_series = [], []
    turnover = 0.0; fees = 0.0; funding_tot = 0.0; n_trades = 0; maker_fills = 0; total_fills = 0
    trades: List[Dict] = []
    prev_leg1 = prev_leg2 = 0.0
    open_dir = 0; open_bar = 0; holding_runs: List[int] = []

    lo, hi = (test[0], test[-1]) if len(test) else (0, -1)
    for t in range(lo, hi):
        beta_t = beta[t] if np.isfinite(beta[t]) else 1.0
        target = risk.size_target(Qf[t], beta_t)
        info = twoleg.rebalance(target, beta_t, p1[t], p2[t],
                                book_spread_bps=book[t], vol=vol[t], ofi=ofi[t])
        c = info["costs"]
        traded = abs(twoleg.leg1.notional - prev_leg1) + abs(twoleg.leg2.notional - prev_leg2)
        if traded > 1e-9:
            n_trades += 1; total_fills += 1
        turnover += traded
        fees += c.fee + c.fixed
        # mark-to-market over [t, t+1]
        r1 = p1[t + 1] / p1[t] - 1.0
        r2 = p2[t + 1] / p2[t] - 1.0
        gross = twoleg.leg1.notional * r1 + twoleg.leg2.notional * r2
        fund = cost.funding_cost(abs(twoleg.net_exposure()))
        funding_tot += fund
        bar_pnl = gross - c.total - fund
        equity += bar_pnl
        pnl.append(bar_pnl); eq_series.append(equity)
        risk.update_equity(equity, ts[t])
        # trade log on position open/close
        cur_dir = int(np.sign(twoleg.leg1.notional))
        if cur_dir != open_dir:
            if open_dir != 0:
                holding_runs.append(t - open_bar)
                trades.append({"exit_ts": float(ts[t]), "entry_ts": float(ts[open_bar]),
                               "direction": open_dir, "hold_bars": t - open_bar,
                               "beta": float(beta_t), "z_entry": float(prep["spread"].zscore[open_bar])
                               if np.isfinite(prep["spread"].zscore[open_bar]) else None})
            open_dir = cur_dir; open_bar = t
        prev_leg1 = twoleg.leg1.notional; prev_leg2 = twoleg.leg2.notional
        maker_fills += 0  # maker accounting simplified (fill model tags maker at source)

    return {"pnl": np.array(pnl), "equity": np.array(eq_series), "turnover": turnover,
            "fees": fees, "funding": funding_tot, "n_trades": n_trades,
            "maker_ratio": (maker_fills / total_fills) if total_fills else 0.0,
            "trades": trades, "holding_runs": np.array(holding_runs) if holding_runs else np.array([]),
            "max_imbalance": float(twoleg.imbalance_bars), "desync": int(twoleg.desync_incidents),
            "risk": risk.snapshot()}


def _series_for_ui(prep, alpha, v, Qf, acct, test, max_pts: int = 3000):
    lo, hi = (test[0], test[-1]) if len(test) else (0, 0)
    sl = slice(lo, hi)
    ts = prep["ts"][sl]
    stride = max(1, len(ts) // max_pts)
    def dec(a): return np.asarray(a)[::stride].tolist()
    eq = acct["equity"]; cpnl = np.cumsum(acct["pnl"]) if len(acct["pnl"]) else np.array([])
    return {
        "ts": dec(ts), "zscore": dec(prep["spread"].zscore[sl]),
        "spread": dec(prep["spread"].spread[sl]), "beta": dec(prep["spread"].beta[sl]),
        "alpha": dec(alpha[sl]), "velocity": dec(v[sl]), "inventory": dec(Qf[sl]),
        "equity": dec(eq), "cum_pnl": dec(cpnl),
    }


# --------------------------------------------------------------------------- #
#  Walk-forward aggregation
# --------------------------------------------------------------------------- #
def _run_walk_forward(cfg, data, prep, rng) -> Dict:
    folds = make_folds(prep["ts"], cfg.walk_forward,
                       label_horizon_seconds=cfg.alpha.horizon_seconds)
    if not folds:
        # fall back to single split when the timeline is too short for WF
        return run_backtest(cfg, data, walk_forward=False)
    fold_reports = []
    all_pnl = []
    for i, f in enumerate(folds):
        r = _run_fold(cfg, data, prep, f.train, f.test, rng)
        r["fold"] = i
        fold_reports.append({"fold": i, "test_range": r["test_range"],
                             "metrics": r["metrics"], "alpha_metrics": r["alpha_metrics"],
                             "optimizer_diagnostics": {k: r["optimizer_diagnostics"].get(k)
                                                       for k in ("condition_number", "theta_norm",
                                                                 "stability_warning")}})
        all_pnl.append(np.asarray(r["series"]["cum_pnl"]))
    sharpes = [fr["metrics"]["sharpe"] for fr in fold_reports if np.isfinite(fr["metrics"]["sharpe"])]
    return {"walk_forward": True, "n_folds": len(folds), "folds": fold_reports,
            "aggregate": {"median_sharpe": float(np.median(sharpes)) if sharpes else np.nan,
                          "mean_sharpe": float(np.mean(sharpes)) if sharpes else np.nan,
                          "sharpe_dispersion": float(np.std(sharpes)) if sharpes else np.nan},
            "is_demo": bool(data.is_demo), "data_meta": data.meta, "config": cfg.to_dict()}


# --------------------------------------------------------------------------- #
#  Benchmarks (§19) — same test window, same capital, same cost model
# --------------------------------------------------------------------------- #
def _benchmarks(cfg: StrategyConfig, prep: Dict, test: np.ndarray) -> Dict:
    if len(test) < 3:
        return {}
    lo, hi = test[0], test[-1]
    p1 = prep["p1"][lo:hi + 1]; p2 = prep["p2"][lo:hi + 1]
    z = prep["spread"].zscore[lo:hi + 1]
    cap = cfg.risk.initial_capital
    d_step = prep["d_step"]
    cost_bps = cfg.costs.taker_fee_bps + cfg.costs.default_slippage_bps
    out = {}

    def _bh(p):
        r = np.diff(p) / p[:-1]
        pnl = cap * r
        eq = cap + np.cumsum(pnl)
        return compute_metrics(pnl, eq, 0.0, 0, d_step, initial_capital=cap)

    out["buy_hold_leg1"] = _pick(_bh(p1))
    out["buy_hold_leg2"] = _pick(_bh(p2))

    # spread returns per unit long-spread notional (long leg1, short beta leg2 ~ use z sign)
    beta = prep["spread"].beta[lo:hi + 1]
    r1 = np.diff(np.log(p1)); r2 = np.diff(np.log(p2))
    spread_ret = r1 - beta[:-1] * r2                   # per unit spread

    def _pos_strategy(pos):
        pos = np.nan_to_num(pos, nan=0.0)
        notional = pos[:-1] * cfg.risk.maximum_position_per_leg
        pnl = notional * spread_ret
        turn = np.sum(np.abs(np.diff(np.concatenate([[0.0], notional]))))
        pnl = pnl - np.abs(np.diff(np.concatenate([[0.0], notional]))) * cost_bps / 1e4
        eq = cap + np.cumsum(pnl)
        return compute_metrics(pnl, eq, float(turn), int(np.sum(np.diff(np.sign(pos)) != 0)),
                               d_step, initial_capital=cap)

    # classic z entry/exit: +/-1 when |z|>2, flat when |z|<0.5
    classic = np.zeros_like(z); state = 0.0
    cl = []
    for zz in z:
        if not np.isfinite(zz):
            cl.append(state); continue
        if state == 0:
            if zz > 2: state = -1
            elif zz < -2: state = 1
        elif abs(zz) < 0.5:
            state = 0
        cl.append(state)
    out["zscore_classic"] = _pick(_pos_strategy(np.array(cl)))
    out["continuous_neg_z"] = _pick(_pos_strategy(np.clip(-z, -1, 1)))
    return out


def _pick(m: Dict) -> Dict:
    keys = ("net_pnl", "sharpe", "max_drawdown_pct", "return_on_turnover_bps",
            "turnover_usd", "n_trades", "hit_ratio")
    return {k: m.get(k) for k in keys}
