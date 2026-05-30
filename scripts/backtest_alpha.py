"""
backtest_alpha.py — Walk-forward backtest of the top signals from alpha_discovery.

Strategy: when feature X[t] is in top-decile (vs rolling window), enter trade.
Hold for horizon_s seconds. Exit at fixed time.

For each signal, compute:
  - equity curve over test set
  - n trades, win rate, total net PnL
  - Sharpe (annualized, assuming daily aggregation)
  - max drawdown
  - average hold, fee drag

Inputs : reports/alpha_discovery.json + data/processed/seconds_features.parquet
Outputs: reports/backtest_alpha_results.md
         reports/backtest_alpha_equity.csv (per-trade rows)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "processed" / "seconds_features.parquet"
SIGNALS_JSON = ROOT / "reports" / "alpha_discovery.json"
REPORT = ROOT / "reports" / "backtest_alpha_results.md"
TRADES_CSV = ROOT / "reports" / "backtest_alpha_trades.csv"

NOTIONAL_USD = 250.0          # per-trade notional
TAKER_COST_BPS_RT = 9.0       # round-trip taker cost
MAKER_COST_BPS_RT = 3.0
TOP_N_SIGNALS = 25            # backtest the top N each from §3 and §4


def load_data() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    df = df[df["symbol"].apply(lambda x: isinstance(x, str))]
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df = df.dropna(subset=["ts", "mid"])
    df = df.sort_values(["symbol", "ts"]).reset_index(drop=True)
    return df


def load_signals() -> dict:
    return json.loads(SIGNALS_JSON.read_text(encoding="utf-8"))


def backtest_one(g: pd.DataFrame, feature: str, horizon_s: int,
                  threshold_pct: float, side: str,
                  cost_bps_rt: float, notional: float = NOTIONAL_USD) -> dict:
    """
    g: dataframe for one symbol, sorted by ts. Must contain `mid` and `feature`.
    horizon_s: hold time. We use row-shift (1Hz proxy).
    threshold_pct: enter when feature is above (`long`) or below (`short`) the
        rolling threshold computed on train half. We pass a fixed quantile.
    side: 'long' (enter when feature high) or 'short' (enter when feature low)
    Returns dict with per-trade and aggregate stats.
    """
    g = g.dropna(subset=[feature, "mid"]).reset_index(drop=True)
    n = len(g)
    if n < 5000:
        return {"n_trades": 0, "skip_reason": "too few rows"}
    split = int(n * 0.6)
    train = g.iloc[:split]
    test = g.iloc[split:].reset_index(drop=True)

    if side == "long":
        thr = float(np.quantile(train[feature].values, 1 - threshold_pct))
        entry_mask = test[feature].values >= thr
    else:
        thr = float(np.quantile(train[feature].values, threshold_pct))
        entry_mask = test[feature].values <= thr

    mid = test["mid"].values.astype(float)
    ts = test["ts"].values.astype(float)

    # Walk over entries, opening trades. Skip if already in position.
    trades = []
    i = 0
    nt = len(test)
    while i < nt:
        if not entry_mask[i]:
            i += 1
            continue
        # Find exit index: i + horizon_s rows ahead, but careful with non-uniform ts
        # Use row shift since features are ~1Hz.
        exit_i = i + horizon_s
        if exit_i >= nt:
            break
        entry_px = mid[i]
        exit_px = mid[exit_i]
        if entry_px <= 0 or exit_px <= 0:
            i = exit_i + 1
            continue
        gross_bps = (exit_px - entry_px) / entry_px * 10_000
        if side == "short":
            gross_bps = -gross_bps
        net_bps = gross_bps - cost_bps_rt
        pnl_usd = notional * net_bps / 10_000
        trades.append({
            "entry_ts": ts[i], "exit_ts": ts[exit_i],
            "entry_px": entry_px, "exit_px": exit_px,
            "gross_bps": gross_bps, "net_bps": net_bps,
            "pnl_usd": pnl_usd, "hold_rows": horizon_s,
        })
        i = exit_i + 1   # Don't overlap positions

    if not trades:
        return {"n_trades": 0, "skip_reason": "no entries"}

    tr = pd.DataFrame(trades)
    n_trades = len(tr)
    total_pnl = float(tr["pnl_usd"].sum())
    wins = int((tr["pnl_usd"] > 0).sum())
    losses = int((tr["pnl_usd"] < 0).sum())
    wr = wins / n_trades if n_trades else 0.0
    avg_pnl = total_pnl / n_trades if n_trades else 0.0
    gross_total_bps = float(tr["gross_bps"].sum())
    net_total_bps = float(tr["net_bps"].sum())
    avg_gross_bps = gross_total_bps / n_trades if n_trades else 0.0
    avg_net_bps = net_total_bps / n_trades if n_trades else 0.0

    # Sharpe-ish — aggregate to daily
    if n_trades >= 5:
        tr["entry_dt"] = pd.to_datetime(tr["entry_ts"], unit="s")
        daily = tr.set_index("entry_dt")["pnl_usd"].resample("1D").sum()
        if len(daily) >= 2 and daily.std() > 0:
            sharpe = float(daily.mean() / daily.std() * np.sqrt(365))
        else:
            sharpe = 0.0
    else:
        sharpe = 0.0

    # Max drawdown on the cumulative equity curve
    eq = tr["pnl_usd"].cumsum().values
    peak = np.maximum.accumulate(eq)
    dd = peak - eq
    max_dd = float(dd.max()) if len(dd) else 0.0

    return {
        "n_trades": n_trades, "wins": wins, "losses": losses,
        "win_rate": round(wr, 3),
        "total_pnl_usd": round(total_pnl, 3),
        "avg_pnl_usd": round(avg_pnl, 5),
        "avg_gross_bps": round(avg_gross_bps, 3),
        "avg_net_bps": round(avg_net_bps, 3),
        "sharpe_annual": round(sharpe, 2),
        "max_dd_usd": round(max_dd, 3),
        "threshold_pct": threshold_pct, "threshold_value": round(thr, 6),
        "trades_df": tr,
    }


def main():
    t0 = time.time()
    df = load_data()
    signals = load_signals()

    print(f"Loaded {len(df):,} rows. Backtesting top signals…")

    # Collect candidate signals: §3 (maker-profitable) + §4 (taker-profitable)
    # + cross-asset profitable. Dedupe.
    candidates = []
    for r in signals.get("profitable_taker", [])[:TOP_N_SIGNALS]:
        candidates.append({
            "kind": "single",
            "symbol": r["symbol"], "feature": r["feature"],
            "horizon_s": int(r["horizon_s"]),
            "side": "long" if (r.get("spearman_test") or 0) >= 0 else "short",
            "cost_kind": "taker",
            "discovery_decile_bps": r["decile_spread_test_bps"],
            "discovery_net_bps": r["net_taker_bps"],
        })
    for r in signals.get("profitable_maker", [])[:TOP_N_SIGNALS]:
        # Skip if already in taker (it'll be there too in most cases)
        key = (r["symbol"], r["feature"], int(r["horizon_s"]))
        if any((c["symbol"], c["feature"], c["horizon_s"]) == key for c in candidates):
            continue
        candidates.append({
            "kind": "single",
            "symbol": r["symbol"], "feature": r["feature"],
            "horizon_s": int(r["horizon_s"]),
            "side": "long" if (r.get("spearman_test") or 0) >= 0 else "short",
            "cost_kind": "maker",
            "discovery_decile_bps": r["decile_spread_test_bps"],
            "discovery_net_bps": r["net_maker_bps"],
        })

    print(f"  {len(candidates)} signals to backtest")

    results = []
    all_trades = []
    for i, c in enumerate(candidates, 1):
        sym, feat, H = c["symbol"], c["feature"], c["horizon_s"]
        side = c["side"]
        cost = TAKER_COST_BPS_RT if c["cost_kind"] == "taker" else MAKER_COST_BPS_RT

        g = df[df["symbol"] == sym].copy()
        if feat not in g.columns:
            print(f"  [{i}/{len(candidates)}] SKIP {sym} {feat} (missing)")
            continue
        res = backtest_one(g, feat, H, threshold_pct=0.10, side=side,
                           cost_bps_rt=cost)
        if "skip_reason" in res:
            print(f"  [{i}/{len(candidates)}] SKIP {sym} {feat} {H}s ({res['skip_reason']})")
            continue
        # Attach trades to global df
        td = res.pop("trades_df")
        td["signal_id"] = f"{sym}|{feat}|{H}s|{side}|{c['cost_kind']}"
        td["symbol"] = sym
        all_trades.append(td)

        merged = {**c, **res}
        results.append(merged)
        print(f"  [{i}/{len(candidates)}] {sym} {feat} {H}s {side} {c['cost_kind']}: "
              f"trades={res['n_trades']} WR={res['win_rate']:.1%} "
              f"PnL=${res['total_pnl_usd']:+.2f} Sharpe={res['sharpe_annual']:.2f}")

    # Save trades CSV
    if all_trades:
        td_all = pd.concat(all_trades, ignore_index=True)
        td_all.to_csv(TRADES_CSV, index=False)
        print(f"\nTrades CSV -> {TRADES_CSV} ({len(td_all):,} rows)")

    write_report(results)
    print(f"\nDone in {time.time()-t0:.0f}s")


def write_report(results: list[dict]) -> None:
    lines: list[str] = []
    w = lines.append
    w(f"# Backtest of top alpha signals — out-of-sample\n")
    w(f"*Generated {time.strftime('%Y-%m-%dT%H:%M:%S')}*\n")

    if not results:
        w("(no signals produced trades)\n")
        REPORT.write_text("\n".join(lines), encoding="utf-8")
        return

    df = pd.DataFrame(results)

    # §1 Headlines
    w("## 1. Headlines\n")
    profitable = df[df["total_pnl_usd"] > 0]
    w(f"- Signals backtested : **{len(df)}**")
    w(f"- Profitable signals (total PnL > 0) : **{len(profitable)}**")
    w(f"- Total PnL across all signals : **${df['total_pnl_usd'].sum():+.2f}**")
    w(f"- Total trades across all signals : **{int(df['n_trades'].sum()):,}**")
    if len(profitable):
        w(f"- Best signal Sharpe : **{profitable['sharpe_annual'].max():.2f}** "
          f"({profitable.loc[profitable['sharpe_annual'].idxmax(), 'symbol']} | "
          f"{profitable.loc[profitable['sharpe_annual'].idxmax(), 'feature']})")
    w("")

    # §2 Full results table (sorted by Sharpe)
    w("## 2. Full results (sorted by Sharpe annual)\n")
    w("| Symbol | Feature | Horizon | Side | Cost | n_trades | WR | Avg gross bps | Avg net bps | Total PnL $ | Sharpe | Max DD $ | Discovery decile bps |")
    w("|---|---|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for _, r in df.sort_values("sharpe_annual", ascending=False).iterrows():
        w(f"| {r['symbol']} | {r['feature']} | {int(r['horizon_s'])}s | {r['side']} | "
          f"{r['cost_kind']} | {int(r['n_trades'])} | {r['win_rate']:.1%} | "
          f"{r['avg_gross_bps']} | {r['avg_net_bps']} | {r['total_pnl_usd']:+.2f} | "
          f"{r['sharpe_annual']} | {r['max_dd_usd']} | {r['discovery_decile_bps']} |")
    w("")

    # §3 Top-10 by total PnL
    w("## 3. Top 10 by total PnL\n")
    top = df.sort_values("total_pnl_usd", ascending=False).head(10)
    w("| Symbol | Feature | Horizon | Side | Cost | n_trades | WR | Total PnL $ | Sharpe |")
    w("|---|---|---:|---|---|---:|---:|---:|---:|")
    for _, r in top.iterrows():
        w(f"| {r['symbol']} | {r['feature']} | {int(r['horizon_s'])}s | {r['side']} | "
          f"{r['cost_kind']} | {int(r['n_trades'])} | {r['win_rate']:.1%} | "
          f"{r['total_pnl_usd']:+.2f} | {r['sharpe_annual']} |")
    w("")

    # §4 Honest verdict
    w("## 4. Honest verdict\n")
    nt = int(df["n_trades"].sum())
    pos = (df["total_pnl_usd"] > 0).sum()
    neg = (df["total_pnl_usd"] < 0).sum()
    avg_sharpe = df["sharpe_annual"].mean()
    best_sharpe = df["sharpe_annual"].max()
    w(f"- {pos} signals profitable vs {neg} losing.")
    w(f"- Average Sharpe across all {len(df)} signals: **{avg_sharpe:.2f}**.")
    w(f"- Best Sharpe: **{best_sharpe:.2f}**.")
    if pos > neg and avg_sharpe > 0:
        w(f"- **The alpha discovery is validated out-of-sample on independent backtest.** "
          f"Multiple signals show positive Sharpe and net PnL — the top decile "
          f"signal entries DO predict future price moves, after costs.")
    elif pos == 0:
        w(f"- **No signal survived the backtest** — discovery was overfit to the decile spread "
          f"metric. The decile-spread metric counts symmetric averaging; an actual entry-based "
          f"strategy doesn't capture the same edge because not every top-decile sample leads to "
          f"an entry (cooldown, overlap).")
    else:
        w(f"- **Marginal alpha** — some signals work, most don't. Top signals worth a live test "
          f"with caution. Larger sample needed for confidence.")
    w("\n*Backtest is no-overlap (sequential trades). Sharpe annualized assuming daily "
      "aggregation, sqrt(365). Costs applied as fixed bps RT.*")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report -> {REPORT}")


if __name__ == "__main__":
    main()
