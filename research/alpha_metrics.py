"""
alpha_metrics.py — Shared alpha research metrics for notebook + CLI.

All functions are pure pandas/numpy. They are NaN-safe and never raise on
missing columns — callers should check the returned dataframe for emptiness.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd


DEFAULT_HORIZONS_S = (5, 15, 30, 60, 120, 300)
DEFAULT_COSTS_BPS = (8.0, 12.0, 16.0)
DEFAULT_CANDIDATE_SIGNALS = (
    "pressure_score_raw",
    "obi_5",
    "trade_imbalance_10s",
    "vwap_slope_5_30",
    "microprice_pressure",
    "book_flow_alignment",
    "book_flow_divergence",
    "absorption_sell_proxy",
    "absorption_buy_proxy",
    "liquidity_vacuum",
)


# ---------------------------------------------------------------------------
# Loading + forward-return calculation
# ---------------------------------------------------------------------------

def load_features(csv_path: str) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    if "ts" in df.columns:
        df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    if "symbol" not in df.columns:
        df["symbol"] = "UNKNOWN"
    df = df.dropna(subset=["ts", "mid"])
    df = df.sort_values(["symbol", "ts"]).reset_index(drop=True)
    return df


def add_forward_returns(df: pd.DataFrame,
                        horizons_s: tuple = DEFAULT_HORIZONS_S) -> pd.DataFrame:
    """For each symbol, add fwd_ret_<h>s = log(mid[t+h] / mid[t]).

    Implementation uses time-asof reindexing per symbol so missing seconds
    don't introduce off-by-one bias.
    """
    out = []
    for sym, g in df.groupby("symbol", sort=False):
        g = g.copy()
        g["__t"] = g["ts"].astype(float)
        for h in horizons_s:
            target = g["__t"] + float(h)
            # asof merge to find next sample with ts >= target
            right = g[["__t", "mid"]].rename(
                columns={"__t": "__rt", "mid": "__rmid"})
            merged = pd.merge_asof(
                pd.DataFrame({"__target": target}).sort_values("__target"),
                right.sort_values("__rt"),
                left_on="__target", right_on="__rt", direction="forward",
                tolerance=float(h * 2),
            )
            merged.index = target.index
            with np.errstate(divide="ignore", invalid="ignore"):
                fwd = np.log(merged["__rmid"].values / g["mid"].values)
            g[f"fwd_ret_{int(h)}s"] = fwd
        g = g.drop(columns="__t", errors="ignore")
        out.append(g)
    return pd.concat(out, ignore_index=True)


def add_btc_residual(df: pd.DataFrame, btc_symbol: str = "BTC",
                     horizons_s: tuple = DEFAULT_HORIZONS_S,
                     beta_window: int = 600) -> pd.DataFrame:
    """Add fwd_ret_<h>s_resid : residual after rolling-beta hedge vs BTC."""
    if btc_symbol not in df["symbol"].unique():
        return df.copy()

    btc = df[df["symbol"] == btc_symbol].copy().sort_values("ts")
    out_parts = []
    for sym, g in df.groupby("symbol", sort=False):
        g = g.copy().sort_values("ts")
        if sym == btc_symbol:
            for h in horizons_s:
                col = f"fwd_ret_{int(h)}s"
                if col in g.columns:
                    g[f"{col}_resid"] = g[col]
            out_parts.append(g)
            continue
        # asof-merge btc returns by ts onto g
        btc_cols = ["ts"] + [f"fwd_ret_{int(h)}s" for h in horizons_s
                             if f"fwd_ret_{int(h)}s" in btc.columns]
        if len(btc_cols) <= 1:
            out_parts.append(g)
            continue
        merged = pd.merge_asof(
            g[["ts"]].sort_values("ts"),
            btc[btc_cols].sort_values("ts"),
            on="ts", direction="backward", tolerance=2.0,
        )
        for h in horizons_s:
            col = f"fwd_ret_{int(h)}s"
            if col not in g.columns or col not in merged.columns:
                continue
            r_a = g[col].values.astype(float)
            r_b = merged[col].values.astype(float)
            # Rolling beta via pandas
            r_a_s = pd.Series(r_a)
            r_b_s = pd.Series(r_b)
            cov = r_a_s.rolling(beta_window, min_periods=30).cov(r_b_s)
            var = r_b_s.rolling(beta_window, min_periods=30).var()
            beta = cov / var.replace(0, np.nan)
            resid = r_a_s - beta * r_b_s
            g[f"{col}_resid"] = resid.values
        out_parts.append(g)
    return pd.concat(out_parts, ignore_index=True)


# ---------------------------------------------------------------------------
# IC analysis
# ---------------------------------------------------------------------------

def compute_ic_table(df: pd.DataFrame,
                     signals: tuple = DEFAULT_CANDIDATE_SIGNALS,
                     horizons_s: tuple = DEFAULT_HORIZONS_S,
                     residual: bool = False) -> pd.DataFrame:
    rows = []
    for sig in signals:
        if sig not in df.columns:
            continue
        for h in horizons_s:
            col = f"fwd_ret_{int(h)}s"
            if residual:
                col = col + "_resid"
            if col not in df.columns:
                continue
            sub = df[[sig, col]].dropna()
            if len(sub) < 100:
                rows.append({
                    "signal": sig, "horizon_s": h,
                    "n": len(sub), "ic_pearson": np.nan, "ic_spearman": np.nan,
                })
                continue
            try:
                ic_p = sub[sig].corr(sub[col], method="pearson")
            except Exception:
                ic_p = np.nan
            try:
                ic_s = sub[sig].corr(sub[col], method="spearman")
            except Exception:
                ic_s = np.nan
            rows.append({
                "signal": sig, "horizon_s": h,
                "n": len(sub),
                "ic_pearson": ic_p, "ic_spearman": ic_s,
            })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Bucket analysis
# ---------------------------------------------------------------------------

def bucket_analysis(df: pd.DataFrame, signal: str, horizon_s: int,
                    n_buckets: int = 10) -> pd.DataFrame:
    col_ret = f"fwd_ret_{int(horizon_s)}s"
    if signal not in df.columns or col_ret not in df.columns:
        return pd.DataFrame()
    sub = df[[signal, col_ret]].dropna()
    if len(sub) < n_buckets * 20:
        return pd.DataFrame()
    try:
        sub["bucket"] = pd.qcut(sub[signal], q=n_buckets,
                                labels=False, duplicates="drop")
    except Exception:
        return pd.DataFrame()
    grouped = sub.groupby("bucket").agg(
        n=(signal, "size"),
        signal_mean=(signal, "mean"),
        ret_mean=(col_ret, "mean"),
        ret_std=(col_ret, "std"),
        hit_rate=(col_ret, lambda x: float((x > 0).mean())),
    ).reset_index()
    grouped["ret_mean_bps"] = grouped["ret_mean"] * 10_000.0
    return grouped


# ---------------------------------------------------------------------------
# Threshold simulation
# ---------------------------------------------------------------------------

def threshold_simulation(df: pd.DataFrame, signal: str, horizon_s: int,
                         long_q: float = 0.9, short_q: float = 0.1,
                         cost_bps: float = 12.0,
                         spread_filter_bps: Optional[float] = None
                         ) -> dict:
    col_ret = f"fwd_ret_{int(horizon_s)}s"
    if signal not in df.columns or col_ret not in df.columns:
        return {"n_trades": 0, "net_bps": np.nan, "profit_factor": np.nan}
    sub = df[[signal, col_ret, "spread_bps"]].dropna(subset=[signal, col_ret])
    if spread_filter_bps is not None and "spread_bps" in sub.columns:
        sub = sub[sub["spread_bps"] <= spread_filter_bps]
    if len(sub) < 200:
        return {"n_trades": 0, "net_bps": np.nan, "profit_factor": np.nan}
    q_hi = sub[signal].quantile(long_q)
    q_lo = sub[signal].quantile(short_q)
    longs = sub[sub[signal] >= q_hi][col_ret]
    shorts = sub[sub[signal] <= q_lo][col_ret] * (-1.0)
    pnls_bps = pd.concat([longs, shorts]) * 10_000.0 - cost_bps
    if len(pnls_bps) == 0:
        return {"n_trades": 0, "net_bps": np.nan, "profit_factor": np.nan}
    wins = pnls_bps[pnls_bps > 0].sum()
    losses = -pnls_bps[pnls_bps < 0].sum()
    pf = float(wins / losses) if losses > 0 else float("inf")
    return {
        "signal": signal,
        "horizon_s": horizon_s,
        "n_trades": int(len(pnls_bps)),
        "cost_bps": cost_bps,
        "long_q": long_q, "short_q": short_q,
        "mean_pnl_bps": float(pnls_bps.mean()),
        "median_pnl_bps": float(pnls_bps.median()),
        "hit_rate": float((pnls_bps > 0).mean()),
        "profit_factor": pf,
        "net_bps": float(pnls_bps.sum()),
    }


def scan_signals(df: pd.DataFrame,
                 signals: tuple = DEFAULT_CANDIDATE_SIGNALS,
                 horizons_s: tuple = (15, 30, 60, 120),
                 costs_bps: tuple = DEFAULT_COSTS_BPS) -> pd.DataFrame:
    rows = []
    for sig in signals:
        if sig not in df.columns:
            continue
        for h in horizons_s:
            for c in costs_bps:
                rows.append(threshold_simulation(df, sig, h, cost_bps=c))
    return pd.DataFrame([r for r in rows if r.get("n_trades", 0) > 0])


# ---------------------------------------------------------------------------
# Walk-forward
# ---------------------------------------------------------------------------

def walk_forward(df: pd.DataFrame, signal: str, horizon_s: int,
                 train_seconds: float = 3600.0,
                 test_seconds: float = 600.0,
                 cost_bps: float = 12.0,
                 long_q: float = 0.9, short_q: float = 0.1) -> pd.DataFrame:
    """Simple expanding/rolling walk-forward.

    Each fold : compute thresholds on TRAIN window, evaluate on the next
    TEST window. Returns a DataFrame of per-fold net_bps.
    """
    col_ret = f"fwd_ret_{int(horizon_s)}s"
    if signal not in df.columns or col_ret not in df.columns:
        return pd.DataFrame()
    sub = df[["ts", signal, col_ret]].dropna()
    if len(sub) < 1000:
        return pd.DataFrame()
    sub = sub.sort_values("ts").reset_index(drop=True)
    t0 = sub["ts"].iloc[0]
    folds = []
    cursor = t0 + train_seconds
    while cursor + test_seconds <= sub["ts"].iloc[-1]:
        train = sub[(sub["ts"] >= cursor - train_seconds) & (sub["ts"] < cursor)]
        test = sub[(sub["ts"] >= cursor) & (sub["ts"] < cursor + test_seconds)]
        if len(train) < 200 or len(test) < 50:
            cursor += test_seconds
            continue
        q_hi = train[signal].quantile(long_q)
        q_lo = train[signal].quantile(short_q)
        longs = test[test[signal] >= q_hi][col_ret]
        shorts = test[test[signal] <= q_lo][col_ret] * (-1.0)
        pnls = pd.concat([longs, shorts]) * 10_000.0 - cost_bps
        folds.append({
            "fold_start_ts": cursor,
            "n_train": len(train), "n_test": len(test),
            "n_trades": len(pnls),
            "mean_bps": float(pnls.mean()) if len(pnls) else np.nan,
            "net_bps": float(pnls.sum()) if len(pnls) else np.nan,
        })
        cursor += test_seconds
    return pd.DataFrame(folds)


# ---------------------------------------------------------------------------
# Uniqueness test (residualize signal against control factors)
# ---------------------------------------------------------------------------

def signal_residual_ic(df: pd.DataFrame, signal: str, horizon_s: int,
                       controls: tuple = ("spread_bps", "rv_30s",
                                          "obi_5", "trade_imbalance_10s")
                       ) -> dict:
    col_ret = f"fwd_ret_{int(horizon_s)}s"
    if signal not in df.columns or col_ret not in df.columns:
        return {"signal": signal, "horizon_s": horizon_s, "ic_resid": np.nan}
    cols = [signal, col_ret] + [c for c in controls if c in df.columns and c != signal]
    sub = df[cols].dropna()
    if len(sub) < 200 or len(cols) < 3:
        return {"signal": signal, "horizon_s": horizon_s, "ic_resid": np.nan}
    X = sub[cols[2:]].values.astype(float)
    y = sub[signal].values.astype(float)
    # OLS via lstsq
    try:
        Xb = np.column_stack([np.ones(len(X)), X])
        beta, *_ = np.linalg.lstsq(Xb, y, rcond=None)
        resid_signal = y - Xb @ beta
        ic = float(np.corrcoef(resid_signal, sub[col_ret].values)[0, 1])
    except Exception:
        ic = float("nan")
    return {"signal": signal, "horizon_s": horizon_s,
            "n": int(len(sub)), "ic_resid": ic}
