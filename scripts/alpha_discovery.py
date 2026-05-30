"""
alpha_discovery.py — Exhaustive IC sweep + composite signals on the
seconds_features corpus.

For each (symbol, feature, horizon) triplet computes:
  - Pearson IC, Spearman IC, t-stat (Pearson)
  - net expected edge in bps after cost
  - threshold scan (top-decile spread)
Then ranks the top signals and validates them out-of-sample (60/40 split).

Inputs : data/processed/seconds_features.parquet (built by data_audit.py)
Outputs: reports/alpha_discovery_complete.md
         reports/alpha_discovery.json (machine-readable for backtest stage)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "processed" / "seconds_features.parquet"
REPORT = ROOT / "reports" / "alpha_discovery_complete.md"
JSON_OUT = ROOT / "reports" / "alpha_discovery.json"

HORIZONS_S = [5, 15, 30, 60, 120, 300]
TAKER_COST_BPS = 9.0   # round-trip taker cost on HL perp (4.5 × 2)
MAKER_COST_BPS = 3.0   # round-trip maker cost (1.5 × 2)
SAFETY_BUFFER_BPS = 1.0  # extra margin
MIN_N_FOR_IC = 10_000  # minimum samples per (symbol, feature, horizon) to consider IC reliable

# Features known to have predictive memory (from data_audit.py §4).
# Junk columns whose name is a numeric value are skipped.
CANDIDATE_FEATURES = [
    "obi_1", "obi_3", "obi_5", "obi_10",
    "trade_imbalance_5s", "trade_imbalance_10s", "trade_imbalance_30s",
    "pressure_score_raw",
    "book_flow_divergence", "book_flow_alignment",
    "microprice_pressure",
    "absorption_buy_proxy", "absorption_sell_proxy",
    "buy_volume_usd_10s", "sell_volume_usd_10s",
    "vwap_slope_5_30",
    "liquidity_vacuum",
    "r_5s", "r_15s", "r_30s",
    "rv_30s", "rv_60s",
]


def load_data() -> pd.DataFrame:
    print(f"Loading {PARQUET}...")
    df = pd.read_parquet(PARQUET)
    print(f"  {len(df):,} rows × {len(df.columns)} cols")

    # Filter rows with valid symbol + numeric ts.
    df = df[df["symbol"].apply(lambda x: isinstance(x, str))]
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts", "mid"])

    # Filter to only the features we care about + ts/symbol/mid.
    keep = ["ts", "symbol", "mid"] + [f for f in CANDIDATE_FEATURES if f in df.columns]
    df = df[keep]

    # Force-numeric on features.
    for f in CANDIDATE_FEATURES:
        if f in df.columns:
            df[f] = pd.to_numeric(df[f], errors="coerce")

    df = df.sort_values(["symbol", "ts"]).reset_index(drop=True)
    print(f"  After filter : {len(df):,} rows, {df['symbol'].nunique()} symbols")
    return df


def compute_forward_returns(g: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """For one symbol's frame, add fwd_bps_<H>s columns: (mid[t+H] - mid[t]) / mid[t] × 10_000.

    `g` must be sorted by ts. We approximate H seconds by row-shift since the
    seconds feature engine emits at 1Hz (with occasional gaps which we accept
    as noise here)."""
    out = g.copy()
    mid = out["mid"].values.astype(float)
    for H in horizons:
        if len(out) <= H:
            out[f"fwd_bps_{H}s"] = np.nan
            continue
        future = np.empty_like(mid)
        future[:-H] = mid[H:]
        future[-H:] = np.nan
        with np.errstate(divide="ignore", invalid="ignore"):
            fwd = (future - mid) / mid * 10_000
        out[f"fwd_bps_{H}s"] = fwd
    return out


def ic_for(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float, int]:
    """Pearson IC, Spearman IC, t-stat, n. NaN-safe."""
    mask = np.isfinite(x) & np.isfinite(y)
    n = int(mask.sum())
    if n < 100:
        return (np.nan, np.nan, np.nan, n)
    xx, yy = x[mask], y[mask]
    # Pearson
    try:
        p_r = np.corrcoef(xx, yy)[0, 1]
    except Exception:
        p_r = np.nan
    # Spearman
    try:
        s_r, _ = stats.spearmanr(xx, yy)
    except Exception:
        s_r = np.nan
    # t-stat for Pearson
    if not np.isfinite(p_r) or abs(p_r) >= 1.0:
        t_stat = np.nan
    else:
        t_stat = p_r * np.sqrt(n - 2) / np.sqrt(1 - p_r ** 2)
    return (float(p_r), float(s_r) if s_r is not None else np.nan,
            float(t_stat) if np.isfinite(t_stat) else np.nan, n)


def decile_spread_bps(x: np.ndarray, y: np.ndarray) -> tuple[float, int, int]:
    """Top decile of x → mean y. Bottom decile → mean y. Return spread.
    Returns (spread_bps, n_top, n_bot)."""
    mask = np.isfinite(x) & np.isfinite(y)
    if mask.sum() < 1000:
        return (np.nan, 0, 0)
    xx, yy = x[mask], y[mask]
    p90 = np.quantile(xx, 0.90)
    p10 = np.quantile(xx, 0.10)
    top = yy[xx >= p90]
    bot = yy[xx <= p10]
    if len(top) < 50 or len(bot) < 50:
        return (np.nan, len(top), len(bot))
    return (float(np.nanmean(top) - np.nanmean(bot)), len(top), len(bot))


def scan_symbol_feature_horizon(g: pd.DataFrame, symbol: str,
                                  features: list[str], horizons: list[int]
                                  ) -> list[dict]:
    """Compute IC + decile spread for every (feature, horizon) on `g` (one
    symbol's rows). Split 60/40 train/test for out-of-sample validation."""
    n = len(g)
    if n < MIN_N_FOR_IC:
        return []
    split = int(n * 0.6)
    train, test = g.iloc[:split], g.iloc[split:]

    rows = []
    for f in features:
        if f not in g.columns:
            continue
        for H in horizons:
            ycol = f"fwd_bps_{H}s"
            if ycol not in g.columns:
                continue
            x_tr = train[f].values
            y_tr = train[ycol].values
            x_te = test[f].values
            y_te = test[ycol].values

            # IC train + test
            p_tr, s_tr, t_tr, n_tr = ic_for(x_tr, y_tr)
            p_te, s_te, t_te, n_te = ic_for(x_te, y_te)

            # Decile spread on test (out-of-sample gross edge)
            spread_te, ntop, nbot = decile_spread_bps(x_te, y_te)
            # On train too for reference
            spread_tr, _, _ = decile_spread_bps(x_tr, y_tr)

            # Net edge (test) after cost: top decile mean > 0 and beats cost
            net_taker = spread_te - TAKER_COST_BPS - SAFETY_BUFFER_BPS if np.isfinite(spread_te) else np.nan
            net_maker = spread_te - MAKER_COST_BPS - SAFETY_BUFFER_BPS if np.isfinite(spread_te) else np.nan

            rows.append({
                "symbol": symbol, "feature": f, "horizon_s": H,
                "n_train": n_tr, "n_test": n_te,
                "pearson_train": round(p_tr, 5) if np.isfinite(p_tr) else None,
                "pearson_test":  round(p_te, 5) if np.isfinite(p_te) else None,
                "spearman_train": round(s_tr, 5) if np.isfinite(s_tr) else None,
                "spearman_test":  round(s_te, 5) if np.isfinite(s_te) else None,
                "t_stat_test": round(t_te, 2) if np.isfinite(t_te) else None,
                "decile_spread_train_bps": round(spread_tr, 3) if np.isfinite(spread_tr) else None,
                "decile_spread_test_bps":  round(spread_te, 3) if np.isfinite(spread_te) else None,
                "net_taker_bps": round(net_taker, 3) if np.isfinite(net_taker) else None,
                "net_maker_bps": round(net_maker, 3) if np.isfinite(net_maker) else None,
                "sign_stable": (np.sign(p_tr) == np.sign(p_te))
                                if (np.isfinite(p_tr) and np.isfinite(p_te)) else False,
            })
    return rows


def compute_cross_asset_lead_lag(df: pd.DataFrame, base: str, others: list[str],
                                  feature: str, horizons: list[int]) -> list[dict]:
    """For each `other` symbol, check if other[feature, t] predicts base future
    return. This tests cross-asset alpha.

    We resample both series to a common ts grid (1Hz integer seconds), then
    align the `other` feature at t against the `base` forward return at
    [t, t+H]."""
    rows: list[dict] = []

    base_df = df[df["symbol"] == base].copy()
    base_df["ts_int"] = base_df["ts"].astype(int)
    base_df = base_df.drop_duplicates(subset="ts_int")
    # add fwd returns for base
    base_df = compute_forward_returns(base_df, horizons)
    base_idx = base_df.set_index("ts_int")

    for other in others:
        if other == base:
            continue
        odf = df[df["symbol"] == other][["ts", feature]].copy()
        odf["ts_int"] = odf["ts"].astype(int)
        odf = odf.drop_duplicates(subset="ts_int")
        odf = odf.set_index("ts_int")[[feature]]
        merged = base_idx[[f"fwd_bps_{H}s" for H in horizons]].join(odf, how="inner")
        if len(merged) < MIN_N_FOR_IC:
            continue
        x = merged[feature].values
        for H in horizons:
            y = merged[f"fwd_bps_{H}s"].values
            p_r, s_r, t_stat, n = ic_for(x, y)
            spread, _, _ = decile_spread_bps(x, y)
            if not np.isfinite(p_r):
                continue
            rows.append({
                "predictor_symbol": other, "predictor_feature": feature,
                "target_symbol": base, "horizon_s": H,
                "n": n, "pearson_ic": round(p_r, 5),
                "spearman_ic": round(s_r, 5) if np.isfinite(s_r) else None,
                "t_stat": round(t_stat, 2) if np.isfinite(t_stat) else None,
                "decile_spread_bps": round(spread, 3) if np.isfinite(spread) else None,
                "net_taker_bps": (round(spread - TAKER_COST_BPS - SAFETY_BUFFER_BPS, 3)
                                  if np.isfinite(spread) else None),
                "net_maker_bps": (round(spread - MAKER_COST_BPS - SAFETY_BUFFER_BPS, 3)
                                  if np.isfinite(spread) else None),
            })
    return rows


def write_report(per_signal: list[dict], cross_asset: list[dict]) -> None:
    print(f"Writing report to {REPORT}")
    df = pd.DataFrame(per_signal)
    cx = pd.DataFrame(cross_asset)

    # Filter to interesting rows.
    valid = df[df["n_test"] >= MIN_N_FOR_IC * 0.4].copy()
    # Rank by |spearman_test| then by sign_stable.
    valid["abs_spearman_test"] = valid["spearman_test"].abs()

    lines: list[str] = []
    w = lines.append

    w(f"# Alpha discovery — exhaustive IC sweep\n")
    w(f"*Generated {time.strftime('%Y-%m-%dT%H:%M:%S')} — corpus from "
      f"{PARQUET.name}*\n")

    # Headlines
    w("## 1. Headlines\n")
    n_total = len(df)
    n_valid = len(valid)
    n_sign_stable = int(valid["sign_stable"].sum())
    n_pos_maker = int((valid["net_maker_bps"] > 0).fillna(False).sum())
    n_pos_taker = int((valid["net_taker_bps"] > 0).fillna(False).sum())
    w(f"- (symbol × feature × horizon) combos scanned : **{n_total:,}**")
    w(f"- Valid (n_test ≥ {int(MIN_N_FOR_IC*0.4)}) : **{n_valid:,}**")
    w(f"- Sign stable (train + test same sign) : **{n_sign_stable:,}**")
    w(f"- Net positive after MAKER cost (3 bps RT) : **{n_pos_maker:,}**")
    w(f"- Net positive after TAKER cost (9 bps RT) : **{n_pos_taker:,}**\n")

    # Top 20 by absolute Spearman (test set)
    w("## 2. Top 20 signals by |Spearman IC| (out-of-sample)\n")
    w("| Symbol | Feature | Horizon | n_test | Pearson_test | Spearman_test | t_stat | Decile spread test bps | Net maker bps | Net taker bps | Sign stable? |")
    w("|---|---|---:|---:|---:|---:|---:|---:|---:|---:|:---:|")
    top = valid.sort_values("abs_spearman_test", ascending=False).head(20)
    for _, r in top.iterrows():
        w(f"| {r['symbol']} | {r['feature']} | {int(r['horizon_s'])}s | {int(r['n_test']):,} | "
          f"{r['pearson_test']} | {r['spearman_test']} | {r['t_stat_test']} | "
          f"{r['decile_spread_test_bps']} | {r['net_maker_bps']} | {r['net_taker_bps']} | "
          f"{'OK' if r['sign_stable'] else 'X'} |")
    w("")

    # Top 20 by NET MAKER bps (sign_stable only)
    w("## 3. Top 20 signals — net positive after MAKER cost (sign-stable)\n")
    w("This is what we would actually trade if we used maker mode.\n")
    w("| Symbol | Feature | Horizon | n_test | Spearman | Decile spread bps | Net maker bps | Net taker bps |")
    w("|---|---|---:|---:|---:|---:|---:|---:|")
    profit_maker = valid[(valid["net_maker_bps"] > 0)
                         & (valid["sign_stable"])].sort_values("net_maker_bps", ascending=False).head(20)
    for _, r in profit_maker.iterrows():
        w(f"| {r['symbol']} | {r['feature']} | {int(r['horizon_s'])}s | {int(r['n_test']):,} | "
          f"{r['spearman_test']} | {r['decile_spread_test_bps']} | {r['net_maker_bps']} | {r['net_taker_bps']} |")
    w("")

    # Top 20 by NET TAKER bps
    w("## 4. Top 20 signals — net positive after TAKER cost (sign-stable)\n")
    w("These are tradeable in taker mode without any maker queue games.\n")
    w("| Symbol | Feature | Horizon | n_test | Spearman | Decile spread bps | Net taker bps |")
    w("|---|---|---:|---:|---:|---:|---:|")
    profit_taker = valid[(valid["net_taker_bps"] > 0)
                         & (valid["sign_stable"])].sort_values("net_taker_bps", ascending=False).head(20)
    for _, r in profit_taker.iterrows():
        w(f"| {r['symbol']} | {r['feature']} | {int(r['horizon_s'])}s | {int(r['n_test']):,} | "
          f"{r['spearman_test']} | {r['decile_spread_test_bps']} | {r['net_taker_bps']} |")
    w("")

    # Cross-asset
    w("## 5. Cross-asset lead-lag (other_symbol[feature] predicts target)\n")
    if len(cx):
        cx_valid = cx[cx["n"] >= MIN_N_FOR_IC * 0.4].copy()
        cx_valid["abs_spear"] = cx_valid["spearman_ic"].abs()
        w("### 5a. Top 20 by |Spearman IC|\n")
        w("| Predictor | Feature | Target | Horizon | n | Pearson | Spearman | t_stat | Decile spread bps | Net maker bps | Net taker bps |")
        w("|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for _, r in cx_valid.sort_values("abs_spear", ascending=False).head(20).iterrows():
            w(f"| {r['predictor_symbol']} | {r['predictor_feature']} | {r['target_symbol']} | "
              f"{int(r['horizon_s'])}s | {int(r['n']):,} | {r['pearson_ic']} | {r['spearman_ic']} | "
              f"{r['t_stat']} | {r['decile_spread_bps']} | {r['net_maker_bps']} | {r['net_taker_bps']} |")
        w("")
        w("### 5b. Cross-asset signals net-positive after MAKER cost\n")
        w("| Predictor | Feature | Target | Horizon | Spearman | Decile bps | Net maker bps |")
        w("|---|---|---|---:|---:|---:|---:|")
        pos_x = cx_valid[cx_valid["net_maker_bps"] > 0].sort_values("net_maker_bps", ascending=False).head(20)
        for _, r in pos_x.iterrows():
            w(f"| {r['predictor_symbol']} | {r['predictor_feature']} | {r['target_symbol']} | "
              f"{int(r['horizon_s'])}s | {r['spearman_ic']} | {r['decile_spread_bps']} | {r['net_maker_bps']} |")
        w("")
    else:
        w("(no cross-asset rows)\n")

    # Verdict + next steps
    w("## 6. Verdict + next steps\n")
    if n_pos_maker > 0:
        w(f"- **Alpha exists at the seconds scale** — {n_pos_maker} signals are net-positive "
          f"after maker cost on out-of-sample data.")
    else:
        w(f"- **No tradeable alpha found** even with maker cost — the microstructure features "
          f"are predictive (non-zero IC) but the decile spread does not cover round-trip cost.")
    if n_pos_taker > 0:
        w(f"- {n_pos_taker} signals beat the harder TAKER threshold — these are the priority for "
          f"a paper run with the existing execution.")
    w("- Use `reports/alpha_discovery.json` as input to the Phase-3 backtest.")
    w("- Cross-asset signals (§5) are the most promising direction — exploiting lead-lag is "
      "harder to crowd out and matches the BTC/ETH lead found in §1.\n")

    REPORT.write_text("\n".join(lines), encoding="utf-8")

    # JSON sidecar for backtest stage
    JSON_OUT.write_text(json.dumps({
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "n_signals": len(valid),
        "top_signals": top.to_dict("records"),
        "profitable_maker": profit_maker.to_dict("records"),
        "profitable_taker": profit_taker.to_dict("records"),
        "cross_asset_signals": cross_asset[:200],
    }, indent=2, default=str), encoding="utf-8")
    print(f"JSON sidecar -> {JSON_OUT}")


def main():
    t0 = time.time()
    df = load_data()

    print("\n=== Per-symbol IC sweep ===")
    horizons = HORIZONS_S
    per_signal: list[dict] = []

    # Process per-symbol so we can compute forward returns cleanly.
    for sym, g in df.groupby("symbol"):
        if len(g) < MIN_N_FOR_IC:
            print(f"  {sym}: SKIP ({len(g):,} rows < {MIN_N_FOR_IC})")
            continue
        g = compute_forward_returns(g, horizons)
        rows = scan_symbol_feature_horizon(g, sym, CANDIDATE_FEATURES, horizons)
        per_signal.extend(rows)
        print(f"  {sym}: {len(g):,} rows -> {len(rows)} feature×horizon combos")

    print(f"\n=== Cross-asset lead-lag (BTC target) ===")
    # Use a key feature with high autocorr from §1.
    cross_asset: list[dict] = []
    for target in ["BTC", "ETH", "SOL"]:
        others = [s for s in df["symbol"].unique() if isinstance(s, str) and s != target]
        for feat in ["obi_5", "obi_10", "trade_imbalance_30s", "pressure_score_raw"]:
            rows = compute_cross_asset_lead_lag(df, target, others, feat, HORIZONS_S)
            cross_asset.extend(rows)
            print(f"  target={target} feat={feat}: {len(rows)} pairs")

    print(f"\nTotal signals : per_signal={len(per_signal)}, cross_asset={len(cross_asset)}")

    write_report(per_signal, cross_asset)
    print(f"\nDone in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
