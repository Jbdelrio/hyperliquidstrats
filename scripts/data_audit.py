"""
data_audit.py — Audit the seconds_features.csv corpus + build a unified parquet.

Inputs: logs/seconds_features.csv + logs/archive/run_*/seconds_features.csv
Outputs:
  - data/processed/seconds_features.parquet (consolidated, deduplicated)
  - reports/data_audit_2026-05-25.md (audit report)
  - reports/data_audit/ (plots if matplotlib available)
"""
from __future__ import annotations

import glob
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "data" / "processed"
REPORT_DIR = ROOT / "reports"
OUT_DIR.mkdir(parents=True, exist_ok=True)
REPORT_DIR.mkdir(parents=True, exist_ok=True)


def load_all_seconds_features() -> pd.DataFrame:
    """Concat all seconds_features csv files (archive + current). Dedupe on
    (ts, symbol) to handle overlaps between consecutive sessions."""
    files = sorted(
        glob.glob(str(ROOT / "logs" / "archive" / "run_*" / "seconds_features.csv"))
        + [str(ROOT / "logs" / "seconds_features.csv")]
    )
    print(f"Loading {len(files)} files...")
    chunks = []
    for i, f in enumerate(files, 1):
        if not os.path.exists(f):
            continue
        sz_mb = os.path.getsize(f) / 1e6
        print(f"  [{i:2d}/{len(files)}] {Path(f).name} ({sz_mb:.1f}MB)...")
        try:
            d = pd.read_csv(f, low_memory=False)
            if len(d) > 0:
                chunks.append(d)
        except Exception as exc:
            print(f"      SKIP ({exc})")
    if not chunks:
        raise RuntimeError("no seconds_features data found")

    print("Concat + dedupe...")
    df = pd.concat(chunks, ignore_index=True)
    pre = len(df)
    df = df.drop_duplicates(subset=["ts", "symbol"], keep="first")
    df = df.sort_values(["symbol", "ts"]).reset_index(drop=True)
    post = len(df)
    print(f"  {pre:,} -> {post:,} rows after dedup ({pre-post:,} duplicates removed)")
    return df


def save_parquet(df: pd.DataFrame, out_path: Path) -> None:
    """Save as parquet with compression."""
    print(f"Saving parquet to {out_path}...")
    df.to_parquet(out_path, compression="snappy", index=False)
    print(f"  {os.path.getsize(out_path)/1e6:.1f}MB written")


def audit_coverage(df: pd.DataFrame) -> dict:
    """Per-symbol coverage stats."""
    stats = {}
    for sym, g in df.groupby("symbol"):
        ts = g["ts"].astype(float)
        first = ts.min()
        last = ts.max()
        n = len(g)
        elapsed_h = (last - first) / 3600.0
        density = n / max(elapsed_h * 3600, 1)
        # gaps: find ts diffs > 5s (3× sampling interval)
        gaps = ts.diff().dropna()
        big_gaps = (gaps > 5).sum()
        max_gap = gaps.max() if len(gaps) else 0
        stats[sym] = {
            "rows": n, "first_ts": first, "last_ts": last,
            "first_dt": pd.to_datetime(first, unit="s"),
            "last_dt": pd.to_datetime(last, unit="s"),
            "elapsed_hours": round(elapsed_h, 2),
            "density_hz": round(density, 3),
            "big_gaps_5s+": int(big_gaps),
            "max_gap_s": round(float(max_gap), 1),
        }
    return stats


def audit_feature_distributions(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    """Per-feature percentiles, std, missing rate."""
    rows = []
    for f in features:
        if f not in df.columns:
            continue
        s = pd.to_numeric(df[f], errors="coerce")
        nz = s.dropna()
        if len(nz) == 0:
            continue
        rows.append({
            "feature": f,
            "n": len(nz),
            "missing_pct": round(100 * (len(s) - len(nz)) / len(s), 2),
            "mean": round(float(nz.mean()), 6),
            "std": round(float(nz.std()), 6),
            "p1": round(float(nz.quantile(0.01)), 6),
            "p50": round(float(nz.quantile(0.5)), 6),
            "p99": round(float(nz.quantile(0.99)), 6),
            "abs_p50": round(float(nz.abs().quantile(0.5)), 6),
            "abs_p95": round(float(nz.abs().quantile(0.95)), 6),
        })
    return pd.DataFrame(rows)


def autocorrelation(df: pd.DataFrame, symbol: str, feature: str,
                     lags: list[int] = (1, 5, 15, 30, 60)) -> dict:
    """Autocorrelation of a feature at multiple lags (rows = seconds)."""
    s = pd.to_numeric(df[df["symbol"] == symbol][feature], errors="coerce").dropna()
    if len(s) < max(lags) + 100:
        return {f"ac_{lag}s": None for lag in lags}
    out = {}
    for lag in lags:
        out[f"ac_{lag}s"] = round(float(s.autocorr(lag)), 4)
    return out


def lead_lag(df: pd.DataFrame, base: str, others: list[str],
              feature: str = "obi_5", lag_s: int = 1) -> pd.DataFrame:
    """For each `other` symbol, compute correlation of base[t] with other[t+lag].
    Positive = base leads. Negative lag = other leads base."""
    rows = []
    for other in others:
        if other == base:
            continue
        a = df[df["symbol"] == base][["ts", feature]].set_index("ts").rename(columns={feature: f"{base}_{feature}"})
        b = df[df["symbol"] == other][["ts", feature]].set_index("ts").rename(columns={feature: f"{other}_{feature}"})
        merged = a.join(b, how="inner")
        if len(merged) < 1000:
            rows.append({"base": base, "other": other, "n": len(merged),
                         "corr_lag0": None, "corr_lag+1s": None, "corr_lag-1s": None})
            continue
        c0 = merged.iloc[:, 0].corr(merged.iloc[:, 1])
        c_p1 = merged.iloc[:, 0].corr(merged.iloc[:, 1].shift(-lag_s))  # base leads
        c_m1 = merged.iloc[:, 0].corr(merged.iloc[:, 1].shift(lag_s))   # other leads
        rows.append({
            "base": base, "other": other, "n": len(merged),
            "corr_lag0": round(float(c0), 4) if not np.isnan(c0) else None,
            "corr_lag+1s": round(float(c_p1), 4) if not np.isnan(c_p1) else None,
            "corr_lag-1s": round(float(c_m1), 4) if not np.isnan(c_m1) else None,
        })
    return pd.DataFrame(rows)


def write_report(df: pd.DataFrame, coverage: dict, feat_dist: pd.DataFrame,
                  lead_lag_df: pd.DataFrame, autocorrs: dict) -> Path:
    out = REPORT_DIR / "data_audit_2026-05-25.md"
    lines: list[str] = []
    w = lines.append
    w(f"# Data audit — seconds_features corpus\n")
    w(f"*Generated {time.strftime('%Y-%m-%dT%H:%M:%S')} — corpus from "
      f"{Path(OUT_DIR / 'seconds_features.parquet').name}*\n")

    # §1 — corpus overview
    w("## 1. Corpus overview\n")
    w(f"- Total rows : **{len(df):,}**")
    w(f"- Unique symbols : **{df['symbol'].nunique()}**")
    w(f"- Total columns : {len(df.columns)}")
    first = pd.to_datetime(df['ts'].min(), unit='s')
    last  = pd.to_datetime(df['ts'].max(), unit='s')
    span_h = (df['ts'].max() - df['ts'].min()) / 3600
    w(f"- Time span : **{first.isoformat()} -> {last.isoformat()}** ({span_h:.1f}h)")
    cols_per_kind = {
        "L2/depth (obi*)": [c for c in df.columns if c.startswith("obi") or "depth" in c.lower()],
        "spread/mid":      [c for c in df.columns if c in ("spread_bps", "mid", "best_bid", "best_ask", "microprice", "microprice_pressure")],
        "trades/flow":     [c for c in df.columns if "imbalance" in c or "volume" in c or "absorption" in c or "ofi" in c],
        "pressure":        [c for c in df.columns if "pressure" in c],
        "volatility/rv":   [c for c in df.columns if "rv" in c or "vol" in c.lower()],
    }
    for kind, cols in cols_per_kind.items():
        w(f"  - {kind}: {len(cols)} cols -> {cols[:8]}{'...' if len(cols)>8 else ''}")
    w("")

    # §2 — coverage per symbol
    w("## 2. Coverage per symbol\n")
    w("| Symbol | Rows | First | Last | Hours | Density Hz | Big gaps (>5s) | Max gap |")
    w("|---|---:|---|---|---:|---:|---:|---:|")
    for sym, s in sorted(coverage.items(), key=lambda x: -x[1]["rows"]):
        w(f"| {sym} | {s['rows']:,} | {s['first_dt']} | {s['last_dt']} | "
          f"{s['elapsed_hours']} | {s['density_hz']} | {s['big_gaps_5s+']:,} | {s['max_gap_s']}s |")
    w("")

    # §3 — feature distributions
    w("## 3. Feature distributions (top features)\n")
    w("| Feature | n | missing% | mean | std | p1 | p50 | p99 | abs p50 | abs p95 |")
    w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in feat_dist.iterrows():
        w(f"| {r['feature']} | {r['n']:,} | {r['missing_pct']}% | {r['mean']} | "
          f"{r['std']} | {r['p1']} | {r['p50']} | {r['p99']} | {r['abs_p50']} | {r['abs_p95']} |")
    w("")

    # §4 — autocorrelations on BTC
    w("## 4. Autocorrelations on BTC (memory in features)\n")
    w("Higher AC at short lags = signal has memory = predictive window is real.\n")
    w("| Feature | AC 1s | AC 5s | AC 15s | AC 30s | AC 60s |")
    w("|---|---:|---:|---:|---:|---:|")
    for feat, vals in autocorrs.items():
        if vals is None:
            continue
        w(f"| {feat} | {vals.get('ac_1s')} | {vals.get('ac_5s')} | "
          f"{vals.get('ac_15s')} | {vals.get('ac_30s')} | {vals.get('ac_60s')} |")
    w("")

    # §5 — lead-lag
    w("## 5. Lead-lag : BTC obi_5 vs other coins\n")
    w("`corr_lag+1s` > `corr_lag-1s` ⟹ BTC leads. Inverse ⟹ other coin leads BTC.\n")
    w("| Base | Other | n samples | corr lag 0 | corr lag +1s | corr lag -1s | Who leads ? |")
    w("|---|---|---:|---:|---:|---:|---|")
    for _, r in lead_lag_df.iterrows():
        if r["corr_lag+1s"] is None or r["corr_lag-1s"] is None:
            continue
        delta = abs(r["corr_lag+1s"]) - abs(r["corr_lag-1s"])
        who = ("-> BTC leads" if delta > 0.005
               else "← Other leads" if delta < -0.005 else "≈ symmetric")
        w(f"| {r['base']} | {r['other']} | {r['n']:,} | {r['corr_lag0']} | "
          f"{r['corr_lag+1s']} | {r['corr_lag-1s']} | {who} |")
    w("")

    # §6 — verdict + next steps
    w("## 6. Verdict + next steps\n")
    big = feat_dist.sort_values("abs_p95", ascending=False).head(10)["feature"].tolist()
    high_ac = [k for k, v in autocorrs.items() if v and (v.get("ac_5s") or 0) > 0.3]
    w(f"- Features with biggest tail (top abs p95): {big}")
    w(f"- Features with strong 5s memory (autocorr > 0.3) -> predictive: {high_ac}")
    w("- These are the candidates for Phase 2 IC sweep.")
    w("- Lead-lag signals (BTC -> coin) can be exploited as cross-asset features.\n")

    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report -> {out}")
    return out


def main():
    t0 = time.time()
    parquet_path = OUT_DIR / "seconds_features.parquet"

    # Reuse parquet if recent (< 1h old)
    if parquet_path.exists() and (time.time() - parquet_path.stat().st_mtime) < 3600:
        print(f"Reusing existing parquet ({parquet_path})")
        df = pd.read_parquet(parquet_path)
    else:
        df = load_all_seconds_features()
        save_parquet(df, parquet_path)

    print(f"\n=== Audit ({len(df):,} rows) ===\n")
    print("Coverage per symbol...")
    coverage = audit_coverage(df)

    # Pick most important features for analysis
    candidate_feats = [
        c for c in df.columns
        if c not in ("ts", "datetime", "symbol", "best_bid", "best_ask", "mid",
                     "book_stale", "enough_data")
    ]
    print(f"Distributions for {len(candidate_feats)} features...")
    feat_dist = audit_feature_distributions(df, candidate_feats)

    print("Autocorrelations on BTC...")
    top_feats_for_ac = ["obi_5", "obi_10", "trade_imbalance_30s", "spread_bps",
                        "pressure_score_raw", "book_flow_divergence",
                        "microprice_pressure"]
    autocorrs = {}
    for f in top_feats_for_ac:
        if f in df.columns:
            autocorrs[f] = autocorrelation(df, "BTC", f)

    print("Lead-lag BTC vs others on obi_5...")
    # Filter out NaN/non-string symbol values that can sneak in from
    # partial header rows in archived CSVs.
    symbols = sorted(s for s in df["symbol"].unique() if isinstance(s, str))
    lead_lag_df = lead_lag(df, "BTC", symbols, feature="obi_5", lag_s=1)

    print("Writing report...")
    write_report(df, coverage, feat_dist, lead_lag_df, autocorrs)

    print(f"\nDone in {time.time()-t0:.1f}s")


if __name__ == "__main__":
    main()
