#!/usr/bin/env python
"""
analyze_strategy_performance.py — Per-strategy / per-symbol breakdown.

Reads `logs/fills_v9.csv` and (optionally) `logs/regime_adaptations.csv`
to produce a Markdown report with:

  - PnL aggregate per strategy / per symbol
  - winrate, expectancy, profit factor
  - avg slippage / fees per trade
  - trades per hour
  - performance per market regime (if regime log available)
  - performance before vs after each parameter adaptation

Usage :
    python scripts/analyze_strategy_performance.py \
        --fills logs/fills_v9.csv \
        --regime logs/regime_adaptations.csv \
        --out reports/strategy_performance.md
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
    p.add_argument("--regime", default="logs/regime_adaptations.csv")
    p.add_argument("--out", default="reports/strategy_performance.md")
    return p.parse_args()


def _read(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _pf(net: pd.Series) -> float:
    wins = net[net > 0].sum()
    losses = -net[net < 0].sum()
    if losses <= 0:
        return float("inf") if wins > 0 else float("nan")
    return float(wins / losses)


def _md_table(df: pd.DataFrame, max_rows: int = 30) -> str:
    if df is None or df.empty:
        return "_(no data)_"
    return df.head(max_rows).to_markdown(index=False, floatfmt=".4f")


def main() -> int:
    args = _parse_args()
    fills = _read(args.fills)
    regime = _read(args.regime)
    lines: list[str] = []
    lines.append("# Strategy Performance Report")
    lines.append("")
    lines.append(f"- Fills    : `{args.fills}` ({len(fills)} rows)")
    lines.append(f"- Regime   : `{args.regime}` ({len(regime)} rows)")
    lines.append("")

    if fills.empty or "net" not in fills.columns:
        lines.append("_No fills yet._")
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text("\n".join(lines), encoding="utf-8")
        print(f"Wrote {args.out}")
        return 0

    net = pd.to_numeric(fills["net"], errors="coerce").fillna(0.0)
    fee = pd.to_numeric(fills.get("fee"), errors="coerce").fillna(0.0)
    hold = pd.to_numeric(fills.get("hold_s"), errors="coerce").fillna(0.0)
    fills = fills.assign(net=net, fee=fee, hold_s=hold)

    # 1. Aggregate
    lines.append("## 1. Aggregate")
    lines.append(f"- Trades              : {len(fills)}")
    lines.append(f"- Net PnL             : ${net.sum():.4f}")
    lines.append(f"- Total fees          : ${fee.sum():.4f}")
    lines.append(f"- Win rate            : {float((net > 0).mean()):.2%}")
    lines.append(f"- Expectancy / trade  : ${net.mean():.4f}")
    lines.append(f"- Profit factor       : {_pf(net):.2f}")
    cum = net.cumsum()
    dd = (cum - cum.cummax()).min()
    lines.append(f"- Max drawdown        : ${dd:.4f}")
    lines.append(f"- Avg fees / trade    : ${fee.mean():.4f}")
    lines.append(f"- Avg hold (s)        : {hold.mean():.0f}")
    lines.append("")

    # 2. Per strategy
    lines.append("## 2. Per strategy")
    if "strategy" in fills.columns:
        g = fills.groupby("strategy").agg(
            trades=("net", "size"),
            net_total=("net", "sum"),
            expectancy=("net", "mean"),
            win_rate=("net", lambda x: float((x > 0).mean())),
            fees_total=("fee", "sum"),
            avg_hold_s=("hold_s", "mean"),
        ).reset_index()
        g["profit_factor"] = g["strategy"].map(
            {s: _pf(fills[fills["strategy"] == s]["net"]) for s in g["strategy"]}
        )
        g = g.sort_values("net_total", ascending=False)
        lines.append(_md_table(g))
    lines.append("")

    # 3. Per symbol
    lines.append("## 3. Per symbol")
    if "symbol" in fills.columns:
        g = fills.groupby("symbol").agg(
            trades=("net", "size"),
            net_total=("net", "sum"),
            expectancy=("net", "mean"),
            win_rate=("net", lambda x: float((x > 0).mean())),
        ).reset_index().sort_values("net_total", ascending=False)
        lines.append(_md_table(g))
    lines.append("")

    # 4. Slippage / fees per trade
    lines.append("## 4. Cost diagnostics")
    if "total_fees_usd" in fills.columns:
        tf = pd.to_numeric(fills["total_fees_usd"], errors="coerce").dropna()
        if not tf.empty:
            lines.append(f"- Avg total fees / trade : ${tf.mean():.4f}")
    if "exit_slippage_bps" in fills.columns:
        es = pd.to_numeric(fills["exit_slippage_bps"], errors="coerce").dropna()
        if not es.empty:
            lines.append(f"- Avg exit slippage bps  : {es.mean():.2f}")
    fee_share = fee.sum() / max(abs(net.sum()) + fee.sum(), 1e-9) * 100.0
    lines.append(f"- Fees / (|net| + fees)  : {fee_share:.1f}%")
    lines.append("")

    # 5. Trades per hour
    lines.append("## 5. Frequency")
    if "ts" in fills.columns:
        try:
            t = pd.to_datetime(fills["ts"], errors="coerce")
            span_h = max((t.max() - t.min()).total_seconds() / 3600.0, 1e-9)
            lines.append(f"- Trades / hour : {len(fills) / span_h:.2f}")
            lines.append(f"- Window (h)    : {span_h:.2f}")
        except Exception:
            pass
    lines.append("")

    # 6. Per regime (if regime log available)
    lines.append("## 6. Performance per regime")
    if not regime.empty and {"ts", "regime"}.issubset(regime.columns) \
            and "ts" in fills.columns:
        try:
            regime["_ts"] = pd.to_numeric(regime["ts"], errors="coerce")
            fills["_ts"] = pd.to_datetime(fills["ts"], errors="coerce")\
                .astype("int64") / 1e9
            # asof-merge: each fill gets the most-recent preceding regime
            m = pd.merge_asof(
                fills.sort_values("_ts")[["_ts", "net", "symbol"]],
                regime[["_ts", "regime"]].sort_values("_ts"),
                on="_ts", direction="backward", tolerance=600.0,
            )
            g = m.dropna(subset=["regime"]).groupby("regime").agg(
                trades=("net", "size"),
                net_total=("net", "sum"),
                expectancy=("net", "mean"),
                win_rate=("net", lambda x: float((x > 0).mean())),
            ).reset_index().sort_values("net_total", ascending=False)
            lines.append(_md_table(g))
        except Exception as e:
            lines.append(f"_regime join failed: {e}_")
    else:
        lines.append("_(no regime log)_")
    lines.append("")

    # 7. Adaptation events
    lines.append("## 7. Recent parameter adaptations (last 30)")
    if not regime.empty:
        keep = [c for c in ("ts", "strategy", "symbol", "regime",
                            "param_name", "old_value", "new_value", "reason")
                if c in regime.columns]
        lines.append(_md_table(regime[keep].tail(30)))
    else:
        lines.append("_(none)_")
    lines.append("")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
