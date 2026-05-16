#!/usr/bin/env python
"""
analyze_seconds_filtered_paper.py — Post-run analysis of the seconds-
filtered paper trading session.

Reads fills + orders + decisions + seconds features (all optional, NaN-
safe), produces a Markdown report under `reports/`.

Usage :
    python scripts/analyze_seconds_filtered_paper.py \\
        --fills logs/fills_v9.csv \\
        --orders logs/orders_v9.csv \\
        --decisions logs/decisions_v9.csv \\
        --seconds logs/seconds_features.csv \\
        --out reports/seconds_filtered_paper_report.md
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


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--fills", default="logs/fills_v9.csv")
    p.add_argument("--orders", default="logs/orders_v9.csv")
    p.add_argument("--decisions", default="logs/decisions_v9.csv")
    p.add_argument("--seconds", default="logs/seconds_features.csv")
    p.add_argument("--out", default="reports/seconds_filtered_paper_report.md")
    return p.parse_args()


def _safe_read(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _md_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    if df is None or df.empty:
        return "_(no data)_"
    return df.head(max_rows).to_markdown(index=False, floatfmt=".4f")


def _expectancy(net_series: pd.Series) -> float:
    if net_series is None or net_series.empty:
        return float("nan")
    return float(net_series.mean())


def _profit_factor(net_series: pd.Series) -> float:
    if net_series is None or net_series.empty:
        return float("nan")
    wins = net_series[net_series > 0].sum()
    losses = -net_series[net_series < 0].sum()
    if losses <= 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def _max_drawdown(net_series: pd.Series) -> float:
    if net_series is None or net_series.empty:
        return float("nan")
    cum = net_series.cumsum()
    peak = cum.cummax()
    dd = cum - peak
    return float(dd.min())


def _corr_safe(x: pd.Series, y: pd.Series) -> float:
    sub = pd.concat([x, y], axis=1).dropna()
    if len(sub) < 20:
        return float("nan")
    try:
        return float(sub.iloc[:, 0].corr(sub.iloc[:, 1]))
    except Exception:
        return float("nan")


def main() -> int:
    args = _parse_args()
    fills = _safe_read(args.fills)
    orders = _safe_read(args.orders)
    decisions = _safe_read(args.decisions)
    seconds = _safe_read(args.seconds)

    lines: list[str] = []
    lines.append("# Seconds-Filtered Paper Trading Report")
    lines.append("")
    lines.append(f"- Fills file     : `{args.fills}` ({len(fills)} rows)")
    lines.append(f"- Orders file    : `{args.orders}` ({len(orders)} rows)")
    lines.append(f"- Decisions file : `{args.decisions}` ({len(decisions)} rows)")
    lines.append(f"- Seconds file   : `{args.seconds}` ({len(seconds)} rows)")
    lines.append("")

    # --------------------------------------------------------------
    # 1. PnL aggregate
    # --------------------------------------------------------------
    lines.append("## 1. PnL aggregate")
    if not fills.empty and "net" in fills.columns:
        net = pd.to_numeric(fills["net"], errors="coerce").fillna(0.0)
        gross = pd.to_numeric(fills.get("gross"), errors="coerce").fillna(0.0)
        fee = pd.to_numeric(fills.get("fee"), errors="coerce").fillna(0.0)
        hold = pd.to_numeric(fills.get("hold_s"), errors="coerce").fillna(0.0)
        lines.append(f"- Trades              : {len(fills)}")
        lines.append(f"- Net PnL             : ${net.sum():.4f}")
        lines.append(f"- Gross PnL           : ${gross.sum():.4f}")
        lines.append(f"- Total fees          : ${fee.sum():.4f}")
        lines.append(f"- Win rate            : {float((net > 0).mean()):.2%}")
        lines.append(f"- Expectancy per trade: ${_expectancy(net):.4f}")
        lines.append(f"- Profit factor       : {_profit_factor(net):.2f}")
        lines.append(f"- Max drawdown        : ${_max_drawdown(net):.4f}")
        lines.append(f"- Avg fees / trade    : ${fee.mean():.4f}")
        lines.append(f"- Avg hold time       : {hold.mean():.0f}s")
    else:
        lines.append("_No fills yet._")
    lines.append("")

    # --------------------------------------------------------------
    # 2. PnL by strategy
    # --------------------------------------------------------------
    lines.append("## 2. PnL by strategy")
    if not fills.empty and "strategy" in fills.columns:
        net = pd.to_numeric(fills["net"], errors="coerce").fillna(0.0)
        fee = pd.to_numeric(fills.get("fee"), errors="coerce").fillna(0.0)
        grouped = fills.assign(net=net, fee=fee).groupby("strategy").agg(
            trades=("net", "size"),
            net_total=("net", "sum"),
            net_mean=("net", "mean"),
            win_rate=("net", lambda x: float((x > 0).mean())),
            fees_total=("fee", "sum"),
        ).reset_index()
        grouped["profit_factor"] = grouped["strategy"].map(
            {s: _profit_factor(net[fills["strategy"] == s])
             for s in grouped["strategy"]}
        )
        lines.append(_md_table(grouped))
    else:
        lines.append("_(no fills)_")
    lines.append("")

    # --------------------------------------------------------------
    # 3. PnL by symbol
    # --------------------------------------------------------------
    lines.append("## 3. PnL by symbol")
    if not fills.empty and "symbol" in fills.columns:
        net = pd.to_numeric(fills["net"], errors="coerce").fillna(0.0)
        grouped = fills.assign(net=net).groupby("symbol").agg(
            trades=("net", "size"),
            net_total=("net", "sum"),
            net_mean=("net", "mean"),
            win_rate=("net", lambda x: float((x > 0).mean())),
        ).reset_index()
        lines.append(_md_table(grouped))
    else:
        lines.append("_(no fills)_")
    lines.append("")

    # --------------------------------------------------------------
    # 4. Exit reasons
    # --------------------------------------------------------------
    lines.append("## 4. Exit reasons")
    if not fills.empty and "reason" in fills.columns:
        net = pd.to_numeric(fills["net"], errors="coerce").fillna(0.0)
        reasons = fills.assign(net=net).groupby("reason").agg(
            trades=("net", "size"),
            net_total=("net", "sum"),
            net_mean=("net", "mean"),
        ).reset_index().sort_values("trades", ascending=False)
        lines.append(_md_table(reasons))
    else:
        lines.append("_(no fills)_")
    lines.append("")

    # --------------------------------------------------------------
    # 5. Block reasons
    # --------------------------------------------------------------
    lines.append("## 5. Block reasons (decisions log)")
    if not decisions.empty:
        # Decisions logger writes column `reason` for skips; also `blocked_reason`
        col_candidates = [c for c in ("blocked_reason", "reason") if c in decisions.columns]
        if col_candidates:
            col = col_candidates[0]
            block_counts = decisions[col].fillna("").astype(str).str.split(":", n=1).str[0].value_counts()
            df = block_counts.reset_index()
            df.columns = ["block_reason_head", "count"]
            lines.append(_md_table(df, max_rows=25))
            # MarketQuality / Throttle blocks specifically
            mqg_n = decisions[col].astype(str).str.startswith("market_quality").sum() \
                if col in decisions.columns else 0
            thr_n = decisions[col].astype(str).str.startswith("throttle").sum() \
                if col in decisions.columns else 0
            lines.append("")
            lines.append(f"- MarketQualityGate blocks : {mqg_n}")
            lines.append(f"- DecisionThrottle blocks  : {thr_n}")
        else:
            lines.append("_(decisions log has no reason column)_")
    else:
        lines.append("_(no decisions log)_")
    lines.append("")

    # --------------------------------------------------------------
    # 6. Frequency
    # --------------------------------------------------------------
    lines.append("## 6. Frequency")
    if not fills.empty and "ts" in fills.columns:
        try:
            t = pd.to_datetime(fills["ts"], errors="coerce")
            span_h = max((t.max() - t.min()).total_seconds() / 3600.0, 1e-9)
            lines.append(f"- Trades per hour : {len(fills) / span_h:.2f}")
            lines.append(f"- Span (hours)    : {span_h:.2f}")
        except Exception:
            lines.append("_(timestamp parse failed)_")
    lines.append("")

    # --------------------------------------------------------------
    # 7. Feature/PnL correlations
    # --------------------------------------------------------------
    lines.append("## 7. Feature ↔ PnL correlations (entry-time)")
    if not fills.empty and not seconds.empty and "ts" in fills.columns and "ts" in seconds.columns:
        try:
            f = fills.copy()
            f["ts_num"] = pd.to_datetime(f["ts"], errors="coerce").astype("int64") / 1e9
            s = seconds.copy()
            s["ts_num"] = pd.to_numeric(s["ts"], errors="coerce")
            # Per fill, find the closest preceding feature snapshot (within 30s) for the same symbol.
            joined = []
            for sym, g in f.groupby("symbol"):
                ss = s[s["symbol"] == sym].sort_values("ts_num")
                g = g.sort_values("ts_num")
                merged = pd.merge_asof(g, ss, on="ts_num", direction="backward",
                                       tolerance=30.0, suffixes=("_fill", "_feat"))
                joined.append(merged)
            df = pd.concat(joined, ignore_index=True) if joined else pd.DataFrame()
            if not df.empty:
                net = pd.to_numeric(df.get("net"), errors="coerce")
                rows = []
                for feat in ("toxicity_score", "liquidity_score", "ofi_30s",
                             "ofi_60s", "spread_bps", "rv_30s", "rv_60s",
                             "depth_imbalance_10", "pressure_score_raw"):
                    if feat in df.columns:
                        rows.append({"feature": feat,
                                     "corr_with_net": _corr_safe(df[feat], net)})
                lines.append(_md_table(pd.DataFrame(rows)))
            else:
                lines.append("_(no overlap between fills and seconds features)_")
        except Exception as e:
            lines.append(f"_(correlation step failed: {e})_")
    else:
        lines.append("_(need both fills and seconds features)_")
    lines.append("")

    # --------------------------------------------------------------
    # 8. Warnings
    # --------------------------------------------------------------
    lines.append("## 8. Warnings")
    warns: list[str] = []
    if not fills.empty and "net" in fills.columns and "fee" in fills.columns:
        net_sum = pd.to_numeric(fills["net"], errors="coerce").sum()
        gross_sum = pd.to_numeric(fills.get("gross"), errors="coerce").sum() if "gross" in fills.columns else None
        fee_sum = pd.to_numeric(fills["fee"], errors="coerce").sum()
        if gross_sum and gross_sum > 0:
            fee_ratio = fee_sum / gross_sum
            if fee_ratio > 0.30:
                warns.append(f"Fees eat {fee_ratio:.0%} of gross PnL (> 30%)")
        if not fills.empty and "strategy" in fills.columns:
            net = pd.to_numeric(fills["net"], errors="coerce").fillna(0.0)
            for sname, g in fills.groupby("strategy"):
                gnet = net[fills["strategy"] == sname]
                if len(gnet) >= 30 and gnet.mean() < 0:
                    warns.append(f"{sname}: expectancy negative after {len(gnet)} trades")
        if not fills.empty and "symbol" in fills.columns:
            sym_counts = fills["symbol"].value_counts()
            if not sym_counts.empty:
                top = sym_counts.idxmax()
                top_share = sym_counts.max() / len(fills)
                if top_share > 0.60:
                    warns.append(f"{top}: concentrated {top_share:.0%} of all trades")
    if not warns:
        lines.append("_(no warnings)_")
    else:
        for w in warns:
            lines.append(f"- {w}")
    lines.append("")

    # --------------------------------------------------------------
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
