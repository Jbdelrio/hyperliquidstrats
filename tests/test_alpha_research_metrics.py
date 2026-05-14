"""Tests for `research/alpha_metrics.py`."""
import numpy as np
import pandas as pd

from research import alpha_metrics as am


def _synthetic_df(n: int = 600, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ts = np.arange(n) * 1.0
    # mid : random walk
    rets = rng.normal(0, 1e-4, size=n)
    mid = 100 * np.exp(np.cumsum(rets))
    # A signal correlated with future returns
    sig = pd.Series(rets).shift(-5).fillna(0).values + rng.normal(0, 1e-5, size=n)
    df = pd.DataFrame({
        "ts": ts,
        "symbol": "BTC",
        "mid": mid,
        "spread_bps": rng.uniform(2, 5, size=n),
        "obi_5": rng.uniform(-0.5, 0.5, size=n),
        "trade_imbalance_10s": rng.uniform(-0.5, 0.5, size=n),
        "pressure_score_raw": sig,
        "vwap_slope_5_30": rng.normal(0, 1e-5, size=n),
        "microprice_pressure": rng.normal(0, 1e-5, size=n),
        "book_flow_alignment": rng.choice([-1, 0, 1], size=n),
        "book_flow_divergence": rng.normal(0, 0.2, size=n),
        "absorption_sell_proxy": rng.uniform(0, 1e-3, size=n),
        "absorption_buy_proxy": rng.uniform(0, 1e-3, size=n),
        "liquidity_vacuum": rng.normal(0, 1, size=n),
        "rv_30s": rng.uniform(0, 1e-3, size=n),
    })
    return df


def test_add_forward_returns_creates_columns():
    df = _synthetic_df()
    df2 = am.add_forward_returns(df, horizons_s=(5, 30))
    for c in ("fwd_ret_5s", "fwd_ret_30s"):
        assert c in df2.columns
    # Should not error on NaN at the tail
    assert df2["fwd_ret_30s"].tail(40).isna().any()


def test_compute_ic_table_returns_finite_values():
    df = _synthetic_df()
    df = am.add_forward_returns(df, horizons_s=(5, 30))
    ic = am.compute_ic_table(df, horizons_s=(5, 30))
    assert {"signal", "horizon_s", "ic_pearson", "ic_spearman"} <= set(ic.columns)
    # At least one finite IC
    assert ic["ic_pearson"].dropna().shape[0] > 0


def test_bucket_analysis_outputs_table():
    df = _synthetic_df(n=2000)
    df = am.add_forward_returns(df, horizons_s=(5, 30))
    b = am.bucket_analysis(df, "pressure_score_raw", 5, n_buckets=5)
    assert not b.empty
    assert set(b.columns) >= {"bucket", "n", "ret_mean", "hit_rate"}


def test_threshold_simulation_runs_on_synthetic():
    df = _synthetic_df(n=3000)
    df = am.add_forward_returns(df, horizons_s=(30,))
    res = am.threshold_simulation(df, "pressure_score_raw", 30, cost_bps=8.0)
    assert res["n_trades"] > 0


def test_signal_residual_ic_handles_missing_controls():
    df = _synthetic_df(n=500)
    df = am.add_forward_returns(df, horizons_s=(60,))
    # Drop one control col → still survives
    df2 = df.drop(columns=["rv_30s"], errors="ignore")
    res = am.signal_residual_ic(df2, "pressure_score_raw", 60)
    assert "ic_resid" in res
    # Must not raise even when columns missing — value can be nan
    assert "horizon_s" in res


def test_metrics_nan_safety_on_empty_df():
    empty = pd.DataFrame({"ts": [], "symbol": [], "mid": []})
    ic = am.compute_ic_table(empty)
    assert ic.empty or ic["ic_pearson"].isna().all()
    b = am.bucket_analysis(empty, "foo", 5)
    assert b.empty
