"""
backtest_leverage_scalp.py — Leverage- and liquidation-aware micro-scalp backtest.

Goal: honestly test the "tiny margin at extreme leverage, capture a 15-30s
volatility burst, close at +X% on margin" idea.

Unlike scripts/backtest_alpha.py (which computes PnL on a fixed notional and
ignores leverage entirely), this simulator:

  * Sizes a position as  notional = margin * leverage.
  * Walks the *intra-hold price path* second-by-second (the parquet is ~1 Hz),
    so it can detect a LIQUIDATION before the take-profit or time stop fires.
  * Models liquidation as: equity wiped when the adverse return on the
    position reaches  g_liq = -(1 - mm) / L   (mm = maintenance-margin frac).
    On liquidation the trade loses the WHOLE margin (-m). This asymmetry —
    a small favourable target vs. a total-loss stop — is the crux of the
    leverage question.
  * Exit priority per second: liquidation → take-profit (return-on-margin ≥
    tp_ret) → time stop at the horizon.
  * Applies a round-trip taker/maker cost in bps on the notional.

It sweeps a grid of leverage levels for each signal and reports the return on
a fixed $500 account (fixed margin per trade), the liquidation rate, and the
per-trade expectancy — so you can see exactly where leverage stops helping and
starts detonating the account.

Usage:
    python scripts/backtest_leverage_scalp.py
    python scripts/backtest_leverage_scalp.py --margin 20 --tp 0.25 \
        --levs 1,10,25,50,100,150,200 --horizon 30
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

PARQUET = ROOT / "data" / "processed" / "seconds_features.parquet"
SIGNALS_JSON = ROOT / "reports" / "alpha_discovery.json"
REPORT = ROOT / "reports" / "backtest_leverage_scalp.md"

ACCOUNT_USD = 500.0


def load_data() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    df = df[df["symbol"].apply(lambda x: isinstance(x, str))]
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df["mid"] = pd.to_numeric(df["mid"], errors="coerce")
    df = df.dropna(subset=["ts", "mid"])
    df = df.sort_values(["symbol", "ts"]).reset_index(drop=True)
    return df


def simulate_signal(g: pd.DataFrame, feature: str, horizon_s: int, side: str,
                    decile: float, levs: list[float], margin: float,
                    tp_ret: float, cost_bps_rt: float, mm: float = 0.5,
                    account: float = ACCOUNT_USD) -> dict:
    """
    Replay one signal on one symbol across a grid of leverage levels.

    Returns per-leverage aggregate stats. Entries are no-overlap, identical
    across leverage levels (the signal doesn't depend on L), so each leverage
    column sees the SAME entry path — only sizing / liq / exit differ.
    """
    g = g.dropna(subset=[feature, "mid"]).reset_index(drop=True)
    n = len(g)
    if n < 5000:
        return {"skip": "too few rows"}

    split = int(n * 0.6)
    train_vals = g[feature].values[:split]
    if side == "long":
        thr = float(np.quantile(train_vals, 1 - decile))
        entry_mask = g[feature].values >= thr
    else:
        thr = float(np.quantile(train_vals, decile))
        entry_mask = g[feature].values <= thr

    mid = g["mid"].values.astype(float)
    ts = g["ts"].values.astype(float)
    cost = cost_bps_rt / 10_000.0

    # Collect entry indices (no overlap on the test half), reusing the SAME
    # entries for every leverage level.
    entries: list[int] = []
    i = split
    while i < n - 1:
        if not entry_mask[i]:
            i += 1
            continue
        if mid[i] <= 0:
            i += 1
            continue
        entries.append(i)
        i += horizon_s + 1   # no overlap

    out: dict[str, dict] = {}
    for L in levs:
        g_liq = -(1.0 - mm) / L          # adverse return on position at liq
        notional = margin * L
        pnls = []
        n_liq = n_tp = n_time = 0
        for ei in entries:
            entry_px = mid[ei]
            end = min(ei + horizon_s, n - 1)
            path = mid[ei + 1:end + 1]
            if path.size == 0:
                continue
            # signed return on position vs entry, per second
            gpath = (path - entry_px) / entry_px
            if side == "short":
                gpath = -gpath

            exit_kind = "time"
            g_exit = gpath[-1]
            for gt in gpath:
                if gt <= g_liq:               # liquidation hit first
                    exit_kind = "liq"
                    break
                if L * gt >= tp_ret + L * cost:  # tp net of cost reached
                    exit_kind = "tp"
                    g_exit = gt
                    break
            else:
                g_exit = gpath[-1]

            if exit_kind == "liq":
                pnl = -margin                 # whole margin gone
                n_liq += 1
            else:
                pnl = margin * L * (g_exit - cost)
                pnl = max(pnl, -margin)       # can't lose more than margin
                if exit_kind == "tp":
                    n_tp += 1
                else:
                    n_time += 1
            pnls.append(pnl)

        if not pnls:
            out[L] = {"n": 0}
            continue
        arr = np.array(pnls)
        nt = len(arr)
        total = float(arr.sum())
        wins = int((arr > 0).sum())
        out[L] = {
            "n": nt,
            "total_pnl": total,
            "acct_return_pct": 100.0 * total / account,
            "win_rate": 100.0 * wins / nt,
            "liq_rate": 100.0 * n_liq / nt,
            "tp_rate": 100.0 * n_tp / nt,
            "time_rate": 100.0 * n_time / nt,
            "expectancy": total / nt,
            "best": float(arr.max()),
            "worst": float(arr.min()),
            "notional": notional,
        }
    out["_meta"] = {"entries": len(entries), "thr": thr}
    return out


# Signals to stress-test. Mix of the strongest discovery signals (altcoins,
# 120-300s) and an "extreme" short-horizon config matching the user's idea.
def default_signals() -> list[dict]:
    return [
        # The strongest validated decile signals (long-horizon altcoin alpha)
        {"symbol": "WLD", "feature": "liquidity_vacuum", "horizon_s": 300, "side": "long", "label": "WLD LV 300s"},
        {"symbol": "INJ", "feature": "liquidity_vacuum", "horizon_s": 300, "side": "long", "label": "INJ LV 300s"},
        {"symbol": "INJ", "feature": "trade_imbalance_30s", "horizon_s": 120, "side": "long", "label": "INJ TI 120s"},
        {"symbol": "WLD", "feature": "obi_10", "horizon_s": 120, "side": "long", "label": "WLD OBI 120s"},
        # The user's "extreme" idea: very short horizon, high-vol alts
        {"symbol": "WLD", "feature": "rv_60s", "horizon_s": 30, "side": "long", "label": "WLD rv 30s (burst)"},
        {"symbol": "BANANA", "feature": "rv_30s", "horizon_s": 30, "side": "long", "label": "BANANA rv 30s (burst)"},
        {"symbol": "KAITO", "feature": "rv_30s", "horizon_s": 15, "side": "long", "label": "KAITO rv 15s (burst)"},
    ]


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--margin", type=float, default=20.0, help="USD margin per trade")
    ap.add_argument("--tp", type=float, default=0.25, help="take-profit as return-on-margin (0.25 = +25%%)")
    ap.add_argument("--levs", default="1,10,25,50,100,150,200")
    ap.add_argument("--decile", type=float, default=0.10)
    ap.add_argument("--cost_bps", type=float, default=9.0, help="round-trip taker cost bps")
    ap.add_argument("--mm", type=float, default=0.5, help="maintenance margin fraction of initial")
    ap.add_argument("--horizon", type=int, default=0, help="override every signal's horizon (0 = keep)")
    args = ap.parse_args()

    levs = [float(x) for x in args.levs.split(",") if x.strip()]
    print(f"Loading parquet…")
    df = load_data()
    print(f"  {len(df):,} rows.  Account ${ACCOUNT_USD:.0f}, margin ${args.margin:.0f}/trade, "
          f"tp +{args.tp*100:.0f}% on margin, cost {args.cost_bps}bps RT, mm {args.mm}")

    signals = default_signals()
    if args.horizon > 0:
        for s in signals:
            s["horizon_s"] = args.horizon

    lines: list[str] = []
    w = lines.append
    w("# Leverage + liquidation-aware micro-scalp backtest\n")
    w(f"*margin ${args.margin:.0f}/trade · TP +{args.tp*100:.0f}% on margin · "
      f"cost {args.cost_bps}bps RT · maint-margin {args.mm} · account ${ACCOUNT_USD:.0f}*\n")
    w("Liquidation = adverse return on position reaches `-(1-mm)/L`; on a "
      "liquidation the trade loses **100% of margin**. Exit priority per "
      "second: liquidation → take-profit → time stop.\n")

    for sig in signals:
        g = df[df["symbol"] == sig["symbol"]].copy()
        if sig["feature"] not in g.columns:
            print(f"SKIP {sig['label']} (feature missing)")
            continue
        res = simulate_signal(
            g, sig["feature"], sig["horizon_s"], sig["side"], args.decile,
            levs, args.margin, args.tp, args.cost_bps, args.mm)
        if "skip" in res:
            print(f"SKIP {sig['label']} ({res['skip']})")
            continue
        meta = res.pop("_meta")
        print(f"\n=== {sig['label']}  ({meta['entries']} entries) ===")
        w(f"\n## {sig['label']}  ·  {sig['symbol']} {sig['feature']} "
          f"{sig['horizon_s']}s {sig['side']}  ·  {meta['entries']} trades\n")
        w("| Leverage | Notional | Acct return % | Total PnL $ | Win % | Liq % | TP % | Time % | Expectancy $ | Worst $ |")
        w("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for L in levs:
            r = res[L]
            if r.get("n", 0) == 0:
                continue
            print(f"  L={L:>5.0f}x  acct={r['acct_return_pct']:+7.1f}%  "
                  f"pnl=${r['total_pnl']:+8.2f}  liq={r['liq_rate']:4.0f}%  "
                  f"tp={r['tp_rate']:4.0f}%  exp=${r['expectancy']:+.3f}")
            w(f"| {L:.0f}x | ${r['notional']:.0f} | {r['acct_return_pct']:+.1f}% | "
              f"${r['total_pnl']:+.2f} | {r['win_rate']:.0f}% | {r['liq_rate']:.0f}% | "
              f"{r['tp_rate']:.0f}% | {r['time_rate']:.0f}% | ${r['expectancy']:+.3f} | "
              f"${r['worst']:+.2f} |")

    w("\n## How to read this\n")
    w("- **Return on margin per trade = L · (g − cost)** where `g` is the price "
      "move over the hold. Leverage multiplies the *net* edge `(g − cost)`. If "
      "`g < cost` on average, higher L loses money **faster**, not slower.\n")
    w("- **Liquidation is asymmetric**: a winning trade makes a fraction of "
      "margin (the TP), but a liquidation loses **all** of it. As L rises the "
      "liquidation distance `~0.5/L` shrinks, the liq rate climbs, and the "
      "account return collapses even when the underlying signal has positive "
      "low-leverage edge.\n")
    w("- The leverage level that **maximises account return** (not the highest "
      "one) is the only one worth running. Beyond it you are paying the "
      "liquidation tax.\n")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
