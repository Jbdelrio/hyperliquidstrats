#!/usr/bin/env python
"""
run_alpha_research.py — CLI runner for the seconds-feature alpha framework.

Reads `logs/seconds_features.csv`, computes forward returns + BTC
residuals, scans candidate signals for IC / bucket / threshold sim /
walk-forward / uniqueness, then writes a Markdown report.

Robust to :
  - missing input file (clear message, non-zero exit),
  - missing columns (skipped, not crash),
  - tiny datasets (each metric returns NaN rather than raising).

Usage :
  python scripts/run_alpha_research.py \
      --features logs/seconds_features.csv \
      --out reports/alpha_research_report.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import numpy as np
import pandas as pd

from research import alpha_metrics as am


def _parse_args():
    p = argparse.ArgumentParser(description="Alpha research CLI (offline).")
    p.add_argument("--features", default="logs/seconds_features.csv")
    p.add_argument("--out", default="reports/alpha_research_report.md")
    p.add_argument("--horizons", default="5,15,30,60,120,300")
    p.add_argument("--costs-bps", default="8,12,16")
    p.add_argument("--beta-window", type=int, default=600)
    return p.parse_args()


def _md_table(df: pd.DataFrame, max_rows: int = 25) -> str:
    if df is None or df.empty:
        return "_(no rows)_"
    if len(df) > max_rows:
        df = df.head(max_rows)
    return df.to_markdown(index=False, floatfmt=".4f")


def main() -> int:
    args = _parse_args()
    features_path = Path(args.features)
    if not features_path.exists():
        msg = (f"Input file not found: {features_path}\n"
               "Run `python engine_v9.py --paper --config "
               "config/presets/paper_500_alpha_research.json` first.")
        print(msg, file=sys.stderr)
        return 2

    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    costs = tuple(float(x) for x in args.costs_bps.split(",") if x.strip())

    df = am.load_features(str(features_path))
    if df.empty:
        print("Empty features file.", file=sys.stderr)
        return 2
    df = am.add_forward_returns(df, horizons_s=horizons)
    df = am.add_btc_residual(df, horizons_s=horizons,
                             beta_window=args.beta_window)

    # ---- IC tables -------------------------------------------------------
    ic_raw = am.compute_ic_table(df, horizons_s=horizons, residual=False)
    ic_resid = am.compute_ic_table(df, horizons_s=horizons, residual=True)

    # ---- Threshold scan --------------------------------------------------
    scan = am.scan_signals(df, horizons_s=(15, 30, 60, 120), costs_bps=costs)
    if not scan.empty:
        scan = scan.sort_values("net_bps", ascending=False)
        top_scan = scan.head(20)
    else:
        top_scan = scan

    # ---- Bucket analysis for top signal ----------------------------------
    bucket_md = "_(no candidate)_"
    if not ic_raw.empty:
        ic_pick = ic_raw.dropna(subset=["ic_spearman"]).copy()
        if not ic_pick.empty:
            ic_pick["abs_ic"] = ic_pick["ic_spearman"].abs()
            top = ic_pick.sort_values("abs_ic", ascending=False).iloc[0]
            bdf = am.bucket_analysis(df, top["signal"], int(top["horizon_s"]))
            if not bdf.empty:
                bucket_md = (f"**{top['signal']}**, horizon={int(top['horizon_s'])}s\n\n"
                             + _md_table(bdf))

    # ---- Walk-forward ----------------------------------------------------
    wf_rows = []
    if not ic_raw.empty:
        candidates = (ic_raw.dropna(subset=["ic_spearman"])
                      .assign(abs_ic=lambda d: d["ic_spearman"].abs())
                      .sort_values("abs_ic", ascending=False)
                      .head(5))
        for _, r in candidates.iterrows():
            wf = am.walk_forward(df, r["signal"], int(r["horizon_s"]),
                                 cost_bps=costs[1] if len(costs) > 1 else costs[0])
            if wf.empty:
                wf_rows.append({"signal": r["signal"],
                                "horizon_s": r["horizon_s"],
                                "n_folds": 0,
                                "mean_net_bps_per_fold": np.nan})
            else:
                wf_rows.append({"signal": r["signal"],
                                "horizon_s": r["horizon_s"],
                                "n_folds": len(wf),
                                "mean_net_bps_per_fold": float(wf["net_bps"].mean()),
                                "positive_folds": int((wf["net_bps"] > 0).sum())})
    wf_df = pd.DataFrame(wf_rows)

    # ---- Uniqueness ------------------------------------------------------
    uniq_rows = []
    for sig in am.DEFAULT_CANDIDATE_SIGNALS:
        if sig in df.columns:
            uniq_rows.append(am.signal_residual_ic(df, sig, 60))
    uniq_df = pd.DataFrame(uniq_rows)

    # ---- Compose report --------------------------------------------------
    parts = [
        "# Alpha Research Report",
        "",
        f"- Input : `{features_path}`",
        f"- Rows  : {len(df):,}",
        f"- Symbols : {sorted(df['symbol'].unique().tolist())}",
        f"- Horizons (s) : {list(horizons)}",
        f"- Costs (bps) : {list(costs)}",
        "",
        "## 1. Information Coefficient — raw forward returns",
        _md_table(ic_raw, max_rows=80),
        "",
        "## 2. Information Coefficient — BTC-residualized forward returns",
        _md_table(ic_resid, max_rows=80),
        "",
        "## 3. Threshold simulation (top 20)",
        _md_table(top_scan, max_rows=20),
        "",
        "## 4. Bucket analysis (best raw-IC candidate)",
        bucket_md,
        "",
        "## 5. Walk-forward (top-5 candidates by |IC|)",
        _md_table(wf_df, max_rows=20),
        "",
        "## 6. Uniqueness — IC of signal *residualized against controls*",
        _md_table(uniq_df, max_rows=40),
        "",
        "## 7. Validation gates (reminder)",
        "",
        "A signal becomes a candidate alpha **only when** :",
        "1. Spearman IC is stable and non-zero across days.",
        "2. Bucket analysis is monotone (or coherent) and not driven by 1 extreme bucket.",
        "3. Threshold-sim net bps is positive AFTER costs.",
        "4. Walk-forward profit factor > 1 and folds mostly positive.",
        "5. BTC-residual IC is non-zero (not just beta).",
        "6. Residual IC (vs spread / vol / OBI / TI controls) is non-zero.",
        "7. Performance is not concentrated in 1 symbol or 1 day.",
        "",
        "_None of these gates are auto-applied — read the tables._",
    ]
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {out_path} ({len(df):,} rows analysed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
