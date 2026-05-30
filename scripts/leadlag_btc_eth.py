"""
leadlag_btc_eth.py — Do BTC / ETH lead the altcoins? At what lag, and is it tradeable?

Two resolutions:
  A) 1-minute candles (data/processed/hl_candles_1m.parquet, top-20, ~3.5d):
       * contemporaneous beta of each alt to BTC and to ETH (who is coupled),
       * lagged predictive corr  corr(BTC_ret[t-k], alt_ret[t])  for k = 0..5 min
         → a positive peak at k>0 means BTC LEADS the alt by k minutes,
       * event study: after a top-decile |BTC move| minute, the alt's average
         same-direction return over the next k minutes (the tradeable reaction).
  B) Seconds (data/processed/seconds_features.parquet, ~1 Hz, 4.6d):
       * same lagged predictive corr at k = 5/15/30 s on a common 1 s grid,
         to see whether the lead-lag lives sub-minute (and is thus hard to act on).

Honest framing: on liquid venues the lead-lag is usually arbitraged toward 0;
SOL/ETH track BTC almost contemporaneously (high beta, ~0 lag), while smaller
alts may show a small but real lag. This script measures exactly which.

Output: reports/leadlag_btc_eth.md
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROC = ROOT / "data" / "processed"
REPORT = ROOT / "reports" / "leadlag_btc_eth.md"

LEADERS = ["BTC", "ETH"]


# ── A) 1-minute lead-lag ─────────────────────────────────────────────────────

def minute_panel() -> pd.DataFrame:
    df = pd.read_parquet(PROC / "hl_candles_1m.parquet")
    df["minute"] = (df["ts_open"] // 60_000).astype("int64")
    wide = df.pivot_table(index="minute", columns="symbol", values="c", aggfunc="last")
    wide = wide.sort_index()
    # keep minutes where the leaders exist
    wide = wide.dropna(subset=[c for c in LEADERS if c in wide.columns], how="any")
    return wide


def analyse_minute(wide: pd.DataFrame, max_lag: int = 5) -> dict:
    rets = np.log(wide).diff()
    coins = [c for c in wide.columns if c not in LEADERS]
    out = {}
    for leader in LEADERS:
        if leader not in rets.columns:
            continue
        lr = rets[leader]
        rows = []
        for c in coins:
            ar = rets[c]
            pair = pd.concat([lr, ar], axis=1).dropna()
            if len(pair) < 500:
                continue
            x = pair.iloc[:, 0].to_numpy()
            y = pair.iloc[:, 1].to_numpy()
            beta = float(np.cov(x, y)[0, 1] / (np.var(x) + 1e-18))
            corr0 = float(np.corrcoef(x, y)[0, 1])
            # lagged predictive corr: leader at t-k vs alt at t
            lag_corr = {}
            for k in range(0, max_lag + 1):
                if k == 0:
                    lag_corr[k] = corr0
                else:
                    xx = x[:-k]; yy = y[k:]
                    lag_corr[k] = float(np.corrcoef(xx, yy)[0, 1]) if len(xx) > 100 else np.nan
            # also alt leading leader (negative lag) to check direction
            lead_back = {}
            for k in range(1, max_lag + 1):
                xx = x[k:]; yy = y[:-k]
                lead_back[k] = float(np.corrcoef(xx, yy)[0, 1]) if len(xx) > 100 else np.nan
            best_fwd_k = max(range(1, max_lag + 1), key=lambda k: (lag_corr[k] if np.isfinite(lag_corr[k]) else -9))
            rows.append({"coin": c, "beta": beta, "corr0": corr0,
                         "lag_corr": lag_corr, "lead_back": lead_back,
                         "best_fwd_k": best_fwd_k, "best_fwd_corr": lag_corr[best_fwd_k]})
        out[leader] = rows
    return out


def event_study_minute(wide: pd.DataFrame, leader: str = "BTC",
                       horizon: int = 3, decile: float = 0.9) -> list[dict]:
    """After a big |leader| minute, alt's avg same-direction return over next H min."""
    rets = np.log(wide).diff()
    if leader not in rets.columns:
        return []
    lr = rets[leader]
    thr = lr.abs().quantile(decile)
    big = lr[lr.abs() >= thr]
    coins = [c for c in wide.columns if c not in LEADERS]
    res = []
    fwd_alt = {c: np.log(wide[c]).diff(horizon).shift(-horizon) for c in coins}
    for c in coins:
        f = fwd_alt[c]
        sub = pd.concat([lr.rename("lr"), f.rename("fwd")], axis=1).loc[big.index].dropna()
        if len(sub) < 50:
            continue
        # same-direction reaction: sign(leader move) * alt forward return
        signed = np.sign(sub["lr"]) * sub["fwd"]
        res.append({"coin": c, "n": len(sub),
                    "avg_reaction_bps": float(signed.mean() * 1e4),
                    "hit_rate": float((signed > 0).mean() * 100)})
    res.sort(key=lambda r: -r["avg_reaction_bps"])
    return res


# ── B) Seconds lead-lag ──────────────────────────────────────────────────────

def seconds_grid(symbols: list[str]) -> pd.DataFrame:
    df = pd.read_parquet(PROC / "seconds_features.parquet",
                         columns=["ts", "symbol", "mid"])
    df = df[df["symbol"].isin(symbols)].copy()
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df["mid"] = pd.to_numeric(df["mid"], errors="coerce")
    df = df.dropna(subset=["ts", "mid"])
    df["sec"] = np.floor(df["ts"]).astype("int64")
    wide = df.pivot_table(index="sec", columns="symbol", values="mid", aggfunc="last")
    full = pd.RangeIndex(wide.index.min(), wide.index.max() + 1)
    wide = wide.reindex(full).ffill(limit=30)        # ffill small gaps only
    return wide


def analyse_seconds(wide: pd.DataFrame, leader: str, alts: list[str],
                    w: int = 10, horizons=(5, 15, 30)) -> list[dict]:
    """corr(leader trailing-w-sec return at t, alt forward-H-sec return at t)."""
    if leader not in wide.columns:
        return []
    logp = np.log(wide)
    lead_trail = logp[leader] - logp[leader].shift(w)
    res = []
    for c in alts:
        if c not in wide.columns:
            continue
        row = {"coin": c}
        for H in horizons:
            fwd = logp[c].shift(-H) - logp[c]
            pair = pd.concat([lead_trail, fwd], axis=1).dropna()
            if len(pair) < 1000:
                row[H] = np.nan
                continue
            row[H] = float(np.corrcoef(pair.iloc[:, 0], pair.iloc[:, 1])[0, 1])
        # contemporaneous for reference
        cont = pd.concat([logp[leader].diff(w), logp[c].diff(w)], axis=1).dropna()
        row["contemp"] = float(np.corrcoef(cont.iloc[:, 0], cont.iloc[:, 1])[0, 1]) if len(cont) > 1000 else np.nan
        res.append(row)
    return res


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    lines: list[str] = []
    w = lines.append
    w("# Lead-lag BTC / ETH → altcoins\n")
    w(f"*{time.strftime('%Y-%m-%dT%H:%M:%S')}*\n")
    w("`beta` = alt return per 1 unit leader return (coupling). `corr0` = "
      "contemporaneous corr. `lag_k` = corr(leader[t−k], alt[t]) — a peak at "
      "k>0 means the leader LEADS by k minutes (tradeable). `lead_back` checks "
      "the reverse (alt leading leader).\n")

    wide = minute_panel()
    print(f"Minute panel: {wide.shape[0]} minutes × {wide.shape[1]} coins")
    res = analyse_minute(wide)

    for leader in LEADERS:
        if leader not in res:
            continue
        w(f"\n## A. 1-minute lead-lag — leader {leader}\n")
        w("| Alt | beta | corr0 (k=0) | lag1 | lag2 | lag3 | best k>0 | "
          "rev lag1 (alt→leader) |")
        w("|---|---:|---:|---:|---:|---:|---:|---:|")
        for r in sorted(res[leader], key=lambda x: -x["corr0"]):
            lc = r["lag_corr"]; lb = r["lead_back"]
            w(f"| {r['coin']} | {r['beta']:.2f} | {r['corr0']:.3f} | "
              f"{lc.get(1, float('nan')):.3f} | {lc.get(2, float('nan')):.3f} | "
              f"{lc.get(3, float('nan')):.3f} | k={r['best_fwd_k']}:"
              f"{r['best_fwd_corr']:.3f} | {lb.get(1, float('nan')):.3f} |")
        print(f"  [{leader}] top contemporaneous:",
              ", ".join(f"{r['coin']}({r['corr0']:.2f})"
                        for r in sorted(res[leader], key=lambda x: -x['corr0'])[:6]))

    # event study
    for leader in LEADERS:
        es = event_study_minute(wide, leader, horizon=3)
        if not es:
            continue
        w(f"\n## B. Event study — after a top-decile |{leader}| minute, "
          f"alt same-direction return over next 3 min\n")
        w("| Alt | n events | avg reaction bps | hit-rate % |")
        w("|---|---:|---:|---:|")
        for r in es:
            w(f"| {r['coin']} | {r['n']} | {r['avg_reaction_bps']:+.2f} | {r['hit_rate']:.1f}% |")

    # seconds
    secs_syms = ["BTC", "ETH", "SOL", "WLD", "INJ", "HYPE", "LINK", "ARB", "XRP", "AVAX"]
    try:
        sg = seconds_grid(secs_syms)
        print(f"Seconds grid: {sg.shape[0]} s × {sg.shape[1]} coins")
        for leader in LEADERS:
            alts = [c for c in secs_syms if c not in LEADERS]
            sr = analyse_seconds(sg, leader, alts)
            if not sr:
                continue
            w(f"\n## C. Seconds lead-lag — leader {leader} "
              f"(trailing 10s → alt forward H s)\n")
            w("| Alt | fwd 5s | fwd 15s | fwd 30s | contemp 10s |")
            w("|---|---:|---:|---:|---:|")
            for r in sr:
                w(f"| {r['coin']} | {r.get(5, float('nan')):.3f} | "
                  f"{r.get(15, float('nan')):.3f} | {r.get(30, float('nan')):.3f} | "
                  f"{r.get('contemp', float('nan')):.3f} |")
    except Exception as e:
        w(f"\n*(seconds analysis skipped: {e})*\n")
        print("seconds analysis error:", e)

    w("\n## Lecture\n")
    w("- `beta` élevé + `corr0` élevé + `lag1..3 ≈ 0` ⇒ le coin bouge **avec** "
      "le leader, pas après → pas de lead-lag exploitable (cas SOL/ETH).\n")
    w("- Un `lag_k` (k>0) nettement positif **et** > la corr contemporaine des "
      "secondes ⇒ le leader précède réellement → signal lead-lag tradeable à "
      "l'horizon k.\n")
    w("- L'event study chiffre l'edge concret : `avg reaction bps` après un gros "
      "mouvement du leader, net à comparer au coût (~6 bps maker RT).\n")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
