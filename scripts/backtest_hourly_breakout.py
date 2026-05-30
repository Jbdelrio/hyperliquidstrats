"""
backtest_hourly_breakout.py — validate HourlyBreakoutStrategy on cached 1h candles.

Replays the EXACT production signal (`HourlyBreakoutStrategy.breakout_signal`)
on data/processed/hl_candles_1h.parquet, applying the same ATR + cost gates the
live strategy uses, with a time-stop hold, net of cost, walk-forward 60/40.
Also sweeps (donchian_period, hold_hours) to check the edge isn't a single-point
fluke. Writes reports/backtest_hourly_breakout.md.

Usage:
  python scripts/backtest_hourly_breakout.py
  python scripts/backtest_hourly_breakout.py --coins ZEC,ETH,WLD,XLM --cost_bps 6
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from strategies.hourly_breakout import HourlyBreakoutStrategy   # noqa: E402

PROC = ROOT / "data" / "processed"
REPORT = ROOT / "reports" / "backtest_hourly_breakout.md"

DEFAULT_COINS = ["ZEC", "ETH", "WLD", "XLM", "HYPE", "BTC", "SOL", "INJ", "TON", "NEAR"]


def _atr_bps(h, l, c, i, n=14):
    if i < n:
        return 0.0
    trs = []
    for k in range(i - n + 1, i + 1):
        trs.append(max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1])))
    atr = sum(trs) / len(trs)
    return (atr / c[i] * 1e4) if c[i] > 0 else 0.0


def backtest_coin(df: pd.DataFrame, period: int, hold: int, cost_bps: float,
                  min_atr_bps: float, min_cost_ratio: float,
                  both: bool = True, margin: float = 0.0, lev: float = 0.0) -> dict:
    c = df["c"].to_numpy(float)
    h = df["h"].to_numpy(float)
    l = df["l"].to_numpy(float)
    n = len(c)
    if n < period + hold + 30:
        return {"n": 0}
    pnls_bps = []
    acct = []                     # per-trade $ on a 500 account if leverage given
    i = period
    while i < n - hold:
        sig = HourlyBreakoutStrategy.breakout_signal(list(c[: i + 1]), period)
        if sig == 0 or (sig < 0 and not both):
            i += 1
            continue
        if _atr_bps(h, l, c, i) < min_atr_bps:
            i += 1
            continue
        window = c[i - period:i]
        ch_lo = window.min()
        range_bps = (window.max() - ch_lo) / ch_lo * 1e4 if ch_lo > 0 else 0.0
        if range_bps < min_cost_ratio * cost_bps:
            i += 1
            continue
        g = (c[i + hold] - c[i]) / c[i] * sig
        net_bps = g * 1e4 - cost_bps
        pnls_bps.append(net_bps)
        if margin > 0 and lev > 0:
            # leverage-aware $ with liquidation (adverse path over the hold)
            liq = -(1.0 - 0.5) / lev
            path = (c[i + 1:i + hold + 1] - c[i]) / c[i] * sig
            if path.min() <= liq:
                acct.append(-margin)
            else:
                acct.append(margin * lev * (g - cost_bps / 1e4))
        i += hold
    if not pnls_bps:
        return {"n": 0}
    arr = np.array(pnls_bps)
    k = int(len(arr) * 0.6)
    res = {"n": len(arr), "total_bps": float(arr.sum()),
           "train_bps": float(arr[:k].sum()), "test_bps": float(arr[k:].sum()),
           "wr": float((arr > 0).mean() * 100), "avg_bps": float(arr.mean()),
           "oos_pos": bool(arr[:k].sum() > 0 and arr[k:].sum() > 0)}
    if acct:
        res["acct_pnl"] = float(np.sum(acct))
        res["acct_return_pct"] = float(np.sum(acct) / 500.0 * 100)
        res["liq_rate"] = float(np.mean(np.array(acct) <= -margin) * 100)
    return res


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default=",".join(DEFAULT_COINS))
    ap.add_argument("--cost_bps", type=float, default=6.0)
    ap.add_argument("--min_atr_bps", type=float, default=25.0)
    ap.add_argument("--min_cost_ratio", type=float, default=2.0)
    ap.add_argument("--margin", type=float, default=20.0)
    ap.add_argument("--lev", type=float, default=5.0)
    args = ap.parse_args()

    path = PROC / "hl_candles_1h.parquet"
    if not path.exists():
        print("Run scripts/hl_top20_behavior.py first (needs hl_candles_1h.parquet).")
        return 1
    allc = pd.read_parquet(path)
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]

    lines: list[str] = []
    w = lines.append
    w("# HourlyBreakout — validation backtest (cached HL 1h candles)\n")
    w(f"*{time.strftime('%Y-%m-%dT%H:%M:%S')} · net {args.cost_bps}bps RT · ATR gate "
      f"{args.min_atr_bps}bps · channel ≥ {args.min_cost_ratio}× cost · lev {args.lev}x/${args.margin:.0f}*\n")

    # base config (period=20, hold=4) = the validated point
    w("## Base config — donchian_period=20, hold=4h\n")
    w("| Coin | n | total bps | train | test | WR% | avg bps | OOS+ | acct ret % | liq% |")
    w("|---|---:|---:|---:|---:|---:|---:|:--:|---:|---:|")
    print("Base config period=20 hold=4:")
    for coin in coins:
        df = allc[allc["symbol"] == coin]
        if df.empty:
            continue
        r = backtest_coin(df, 20, 4, args.cost_bps, args.min_atr_bps,
                          args.min_cost_ratio, True, args.margin, args.lev)
        if r["n"] == 0:
            continue
        flag = "✅" if r["oos_pos"] else "—"
        ar = r.get("acct_return_pct", float("nan"))
        liq = r.get("liq_rate", float("nan"))
        print(f"  {coin:6s} n={r['n']:3d} tot={r['total_bps']:+7.0f} "
              f"tr={r['train_bps']:+7.0f} te={r['test_bps']:+7.0f} WR={r['wr']:.0f}% "
              f"{flag} acct={ar:+.1f}%")
        w(f"| {coin} | {r['n']} | {r['total_bps']:+.0f} | {r['train_bps']:+.0f} | "
          f"{r['test_bps']:+.0f} | {r['wr']:.0f}% | {r['avg_bps']:+.1f} | {flag} | "
          f"{ar:+.1f}% | {liq:.0f}% |")

    # parameter sweep — robustness of the OOS+ coins
    w("\n## Parameter sweep (test-half total bps) — is the edge robust?\n")
    periods = [10, 15, 20, 30, 40]
    holds = [2, 4, 6, 12]
    sweep_coins = [c for c in coins if not allc[allc["symbol"] == c].empty][:6]
    for coin in sweep_coins:
        df = allc[allc["symbol"] == coin]
        w(f"\n### {coin} — test-half net bps by (period × hold)\n")
        w("| period \\ hold | " + " | ".join(f"{hh}h" for hh in holds) + " |")
        w("|---|" + "---|" * len(holds))
        for pp in periods:
            row = [f"| {pp}"]
            for hh in holds:
                r = backtest_coin(df, pp, hh, args.cost_bps, args.min_atr_bps,
                                  args.min_cost_ratio, True)
                if r["n"] == 0:
                    row.append(" — ")
                else:
                    mark = "**" if r["test_bps"] > 0 else ""
                    row.append(f" {mark}{r['test_bps']:+.0f}{mark} ")
            w(" | ".join(row) + " |")

    w("\n## Reading\n")
    w("- **OOS+** = train and test both positive at the base config. The sweep "
      "shows whether the test-half edge holds across (period × hold); a coin "
      "that is green across most of the grid is a real edge, a lone green cell "
      "is noise.\n")
    w("- Leverage modelled with liquidation (adverse path over the hold, "
      "loses 100% margin at `-0.5/L`). Keep `lev` ≤ the coin's HL maxLeverage.\n")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
