"""
backtest_leader_filter.py — does the BTC/ETH leader-bias filter improve HourlyBreakout?

Replays the 1h breakout (same `HourlyBreakoutStrategy.breakout_signal`) on the
cached HL 1h candles, on an hour-aligned panel so the leader (BTC/ETH) returns
line up exactly with each alt entry, and compares:
    baseline (no filter)  vs  leader-gated  (same `LeaderBias.gate` as live)
across leader modes and `min_bps` thresholds. Walk-forward 60/40, net of cost.

The point: a good filter REMOVES bad trades — net bps/trade and win-rate should
rise even if total drops (fewer trades). Watch avg_bps and WR, not just total.

Usage:
  python scripts/backtest_leader_filter.py --coins ZEC,WLD,HYPE --cost_bps 6
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
from strategies.leader_bias import LeaderBias                    # noqa: E402

PROC = ROOT / "data" / "processed"
REPORT = ROOT / "reports" / "backtest_leader_filter.md"
LEADERS = ["BTC", "ETH"]


def hour_panel() -> pd.DataFrame:
    df = pd.read_parquet(PROC / "hl_candles_1h.parquet")
    df["hour"] = (df["ts_open"] // 3_600_000).astype("int64")
    close = df.pivot_table(index="hour", columns="symbol", values="c", aggfunc="last").sort_index()
    high = df.pivot_table(index="hour", columns="symbol", values="h", aggfunc="last").sort_index()
    low = df.pivot_table(index="hour", columns="symbol", values="l", aggfunc="last").sort_index()
    return close, high, low


def _atr_bps(h, l, c, i, n=14):
    if i < n:
        return 0.0
    trs = [max(h[k] - l[k], abs(h[k] - c[k - 1]), abs(l[k] - c[k - 1]))
           for k in range(i - n + 1, i + 1)]
    atr = sum(trs) / len(trs)
    return (atr / c[i] * 1e4) if c[i] > 0 else 0.0


def run(close, high, low, coin, period, hold, cost_bps, min_atr, min_cost_ratio,
        leader_mode=None, leader_min_bps=40.0, leader_window=4) -> dict:
    pan = pd.concat([close[coin].rename("c"), high[coin].rename("h"),
                     low[coin].rename("l")] +
                    [close[s].rename(f"L_{s}") for s in LEADERS if s in close.columns],
                    axis=1).dropna(subset=["c", "h", "l"])
    c = pan["c"].to_numpy(float); h = pan["h"].to_numpy(float); l = pan["l"].to_numpy(float)
    lead_cols = {s: pan[f"L_{s}"].to_numpy(float) for s in LEADERS if f"L_{s}" in pan.columns}
    n = len(c)
    if n < period + hold + 30:
        return {"n": 0}
    pnls = []
    i = period
    while i < n - hold:
        sig = HourlyBreakoutStrategy.breakout_signal(list(c[: i + 1]), period)
        if sig == 0:
            i += 1
            continue
        if _atr_bps(h, l, c, i) < min_atr:
            i += 1
            continue
        window = c[i - period:i]
        ch_lo = window.min()
        if (window.max() - ch_lo) / ch_lo * 1e4 < min_cost_ratio * cost_bps:
            i += 1
            continue
        # leader gate
        if leader_mode:
            lr = {}
            for s, arr in lead_cols.items():
                j0 = i - leader_window
                if j0 >= 0 and arr[j0] > 0 and np.isfinite(arr[i]) and np.isfinite(arr[j0]):
                    lr[s] = (arr[i] - arr[j0]) / arr[j0] * 1e4
            if not LeaderBias.gate(sig, lr, leader_min_bps, leader_mode):
                i += 1
                continue
        g = (c[i + hold] - c[i]) / c[i] * sig
        pnls.append(g * 1e4 - cost_bps)
        i += hold
    if not pnls:
        return {"n": 0}
    a = np.array(pnls)
    k = int(len(a) * 0.6)
    return {"n": len(a), "total": float(a.sum()), "avg": float(a.mean()),
            "wr": float((a > 0).mean() * 100),
            "train": float(a[:k].sum()), "test": float(a[k:].sum()),
            "oos": bool(a[:k].sum() > 0 and a[k:].sum() > 0)}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--coins", default="ZEC,WLD,HYPE")
    ap.add_argument("--cost_bps", type=float, default=6.0)
    ap.add_argument("--min_atr_bps", type=float, default=25.0)
    ap.add_argument("--min_cost_ratio", type=float, default=2.0)
    ap.add_argument("--window", type=int, default=4)
    args = ap.parse_args()

    close, high, low = hour_panel()
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    hold_by = {"ZEC": 4, "WLD": 6, "HYPE": 6}

    lines: list[str] = []
    w = lines.append
    w("# Leader-bias filter on HourlyBreakout — A/B\n")
    w(f"*{time.strftime('%Y-%m-%dT%H:%M:%S')} · net {args.cost_bps}bps · leader window "
      f"{args.window}h · A good filter lifts avg_bps & WR (fewer, better trades).*\n")

    modes = [("baseline", None, 0.0),
             ("veto_opposite@40", "veto_opposite", 40.0),
             ("veto_opposite@25", "veto_opposite", 25.0),
             ("require_agree@40", "require_agree", 40.0)]

    for coin in coins:
        if coin not in close.columns:
            continue
        hold = hold_by.get(coin, 4)
        w(f"\n## {coin} (period 20, hold {hold}h)\n")
        w("| Variant | n | total bps | avg bps | WR % | train | test | OOS+ |")
        w("|---|---:|---:|---:|---:|---:|---:|:--:|")
        print(f"\n=== {coin} hold {hold}h ===")
        base_avg = None
        for label, mode, mb in modes:
            r = run(close, high, low, coin, 20, hold, args.cost_bps,
                    args.min_atr_bps, args.min_cost_ratio, mode, mb, args.window)
            if r["n"] == 0:
                continue
            if label == "baseline":
                base_avg = r["avg"]
            flag = "✅" if r["oos"] else "—"
            d = f" (Δavg {r['avg']-base_avg:+.1f})" if base_avg is not None and label != "baseline" else ""
            print(f"  {label:20s} n={r['n']:3d} avg={r['avg']:+.1f}{d} WR={r['wr']:.0f}% "
                  f"tot={r['total']:+.0f} te={r['test']:+.0f} {flag}")
            w(f"| {label} | {r['n']} | {r['total']:+.0f} | {r['avg']:+.1f} | {r['wr']:.0f}% | "
              f"{r['train']:+.0f} | {r['test']:+.0f} | {flag} |")

    w("\n## Reading\n")
    w("- The filter is worth keeping if a gated variant **raises avg bps/trade "
      "and WR** vs baseline (it trades less but cleaner). If avg barely moves, "
      "the leader info is already in the breakout — drop it.\n")
    w("- `veto_opposite` only blocks breakouts fighting a strong leader move; "
      "`require_agree` is stricter (needs a leader pushing the same way).\n")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
