"""
hl_top20_behavior.py — Fetch + behavioural analysis of the HL top-20 coins.

Pulls the maximum candle history Hyperliquid allows (5000 bars/request) at 1m,
15m and 1h for the top-20 perps by 24h notional volume, then characterises how
each coin *behaves* so the strategies can be pointed at the right coin / horizon.

Per coin × timeframe it computes:
  * typical_move_bps   — median |close-to-close| return (the move you must beat)
  * vol_bps            — std of bar returns in bps
  * tradeable_frac     — fraction of bars whose |move| exceeds a round-trip cost
  * autocorr_lag1      — sign of short-term memory: + = momentum, − = reversion
  * variance_ratio_5   — VR(5); >1 trends, <1 mean-reverts
  * efficiency_ratio   — Kaufman ER over the series; high = directional/trending
  * hurst              — rescaled-range-ish exponent; >0.5 trend, <0.5 revert

From those it tags each coin/timeframe TREND / REVERT / NOISE and whether it's
TRADEABLE (typical move clears cost), then writes hypotheses + a per-coin
strategy recommendation.

Outputs:
  data/processed/hl_candles_<tf>.parquet   (raw candles, reusable)
  reports/hl_top20_behavior.md             (analysis + recommendations)

Usage:
  python scripts/hl_top20_behavior.py
  python scripts/hl_top20_behavior.py --cost_bps 6 --top 20 --no-fetch
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HL_API = "https://api.hyperliquid.xyz/info"
PROC = ROOT / "data" / "processed"
REPORT = ROOT / "reports" / "hl_top20_behavior.md"

TFS = {"1m": 60_000, "15m": 900_000, "1h": 3_600_000}
MAX_BARS = 5000


# ── fetch ────────────────────────────────────────────────────────────────────

def top_coins(n: int) -> list[tuple[str, float, int]]:
    d = requests.post(HL_API, json={"type": "metaAndAssetCtxs"}, timeout=20).json()
    uni, ctx = d[0]["universe"], d[1]
    rows = []
    for i, a in enumerate(uni):
        c = ctx[i] if i < len(ctx) else {}
        rows.append((a["name"], float(c.get("dayNtlVlm", 0) or 0),
                     int(a.get("maxLeverage", 0) or 0)))
    rows.sort(key=lambda x: -x[1])
    return rows[:n]


def fetch_candles(coin: str, interval: str, n_bars: int = MAX_BARS,
                  retries: int = 4) -> pd.DataFrame:
    tf_ms = TFS[interval]
    end = int(time.time() * 1000)
    start = end - n_bars * tf_ms
    data = None
    for attempt in range(retries):
        try:
            r = requests.post(HL_API, json={"type": "candleSnapshot", "req": {
                "coin": coin, "interval": interval,
                "startTime": start, "endTime": end}}, timeout=20)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            data = r.json()
            if data:
                break
            time.sleep(0.8 * (attempt + 1))      # empty → backoff & retry
        except Exception as e:
            time.sleep(0.8 * (attempt + 1))
            if attempt == retries - 1:
                print(f"    fetch error {coin} {interval}: {e}")
    if not data:
        return pd.DataFrame()
    df = pd.DataFrame(data)
    for c in ("o", "h", "l", "c", "v"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["n"] = pd.to_numeric(df.get("n", 0), errors="coerce")
    df = df.rename(columns={"t": "ts_open", "T": "ts_close"})
    df["symbol"] = coin
    df["interval"] = interval
    return df.dropna(subset=["c"]).reset_index(drop=True)


# ── behavioural metrics ──────────────────────────────────────────────────────

def variance_ratio(r: np.ndarray, q: int) -> float:
    n = len(r)
    if n < q * 3:
        return float("nan")
    mu = r.mean()
    var1 = np.sum((r - mu) ** 2) / (n - 1)
    rq = np.convolve(r, np.ones(q), mode="valid")        # q-period sums
    varq = np.sum((rq - q * mu) ** 2) / (len(rq) - 1)
    return float(varq / (q * var1)) if var1 > 0 else float("nan")


def efficiency_ratio(close: np.ndarray, window: int = 20) -> float:
    """Kaufman efficiency ratio averaged over rolling `window`-bar segments
    (a single ER over thousands of bars is meaninglessly tiny)."""
    if len(close) < window + 1:
        return float("nan")
    d = np.abs(np.diff(close))
    net = np.abs(close[window:] - close[:-window])
    path = np.convolve(d, np.ones(window), mode="valid")[: len(net)]
    with np.errstate(divide="ignore", invalid="ignore"):
        er = np.where(path > 0, net / path, np.nan)
    return float(np.nanmean(er))


def hurst(close: np.ndarray, max_lag: int = 60) -> float:
    """Hurst via the variance-of-differences scaling of log-prices."""
    logp = np.log(close[close > 0])
    if len(logp) < max_lag * 4:
        return float("nan")
    lags = range(2, max_lag)
    tau = [np.sqrt(np.std(logp[lag:] - logp[:-lag])) for lag in lags]
    tau = np.array(tau)
    good = tau > 0
    if good.sum() < 5:
        return float("nan")
    poly = np.polyfit(np.log(list(lags))[good], np.log(tau)[good], 1)
    return float(poly[0] * 2.0)


def analyse(df: pd.DataFrame, cost_bps: float) -> dict:
    close = df["c"].to_numpy(float)
    if len(close) < 100:
        return {}
    logr = np.diff(np.log(close))
    r_bps = logr * 1e4
    typ = float(np.median(np.abs(r_bps)))
    vol = float(np.std(r_bps))
    tradeable = float(np.mean(np.abs(r_bps) > cost_bps))
    ac1 = float(np.corrcoef(logr[:-1], logr[1:])[0, 1]) if len(logr) > 3 else float("nan")
    vr5 = variance_ratio(logr, 5)
    er = efficiency_ratio(close)
    h = hurst(close)
    # ATR% (high-low range)
    hl = (df["h"].to_numpy(float) - df["l"].to_numpy(float)) / close
    atr_bps = float(np.nanmedian(hl) * 1e4)
    return {"n": len(close), "typ_move_bps": typ, "vol_bps": vol,
            "tradeable_frac": tradeable, "ac1": ac1, "vr5": vr5,
            "eff_ratio": er, "hurst": h, "atr_bps": atr_bps}


def classify(m: dict, cost_bps: float) -> tuple[str, str]:
    """Return (behaviour_tag, tradeable_tag)."""
    ac1 = m.get("ac1", 0.0) or 0.0
    vr5 = m.get("vr5", 1.0)
    h = m.get("hurst", 0.5)
    score = 0
    if ac1 > 0.03:
        score += 1
    if ac1 < -0.03:
        score -= 1
    if np.isfinite(vr5):
        if vr5 > 1.1:
            score += 1
        elif vr5 < 0.9:
            score -= 1
    if np.isfinite(h):
        if h > 0.55:
            score += 1
        elif h < 0.45:
            score -= 1
    tag = "TREND" if score >= 1 else "REVERT" if score <= -1 else "NOISE"
    # tradeable: typical move clears cost AND enough bars exceed cost
    trd = "✓" if (m.get("typ_move_bps", 0) > cost_bps and
                  m.get("tradeable_frac", 0) > 0.35) else "✗"
    return tag, trd


def recommend(coin: str, by_tf: dict, cost_bps: float, maxlev: int) -> str:
    """One-line strategy recommendation from the per-timeframe behaviour."""
    best_tf = max(by_tf, key=lambda tf: by_tf[tf]["m"].get("tradeable_frac", 0))
    bm = by_tf[best_tf]["m"]
    tag = by_tf[best_tf]["tag"]
    if bm.get("typ_move_bps", 0) <= cost_bps:
        return f"AVOID — even the best TF ({best_tf}) median move {bm['typ_move_bps']:.1f}bps < cost {cost_bps}bps. Maker-only or skip."
    if tag == "TREND":
        return f"MOMENTUM/BREAKOUT @ {best_tf} (move {bm['typ_move_bps']:.1f}bps, AC1 {bm['ac1']:+.3f}, ER {bm['eff_ratio']:.2f}). Lev ≤ {maxlev}x."
    if tag == "REVERT":
        return f"MEAN-REVERSION @ {best_tf} (move {bm['typ_move_bps']:.1f}bps, AC1 {bm['ac1']:+.3f}, VR5 {bm['vr5']:.2f}). RSI/Bollinger or decile-reversal."
    return f"NOISE @ {best_tf} — microstructure-only (obi/microprice), maker-first; no bar-level edge."


# ── main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--cost_bps", type=float, default=6.0, help="round-trip cost reference")
    ap.add_argument("--no-fetch", action="store_true", help="reuse cached parquet")
    args = ap.parse_args()
    PROC.mkdir(parents=True, exist_ok=True)

    coins = top_coins(args.top)
    maxlev = {c: ml for c, _, ml in coins}
    vol24 = {c: v for c, v, _ in coins}
    names = [c for c, _, _ in coins]
    print(f"Top {len(names)}: {', '.join(names)}")

    # fetch / load
    cache: dict[str, pd.DataFrame] = {}
    for tf in TFS:
        path = PROC / f"hl_candles_{tf}.parquet"
        if args.no_fetch and path.exists():
            cache[tf] = pd.read_parquet(path)
            print(f"  loaded cache {tf}: {len(cache[tf])} rows")
            continue
        frames = []
        for c in names:
            df = fetch_candles(c, tf)
            if not df.empty:
                frames.append(df)
            print(f"  {tf} {c}: {len(df)} bars")
            time.sleep(0.15)
        fresh = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        # APPEND mode: HL only serves the last 5000 bars per request, so each
        # refetch extends history. Merge with the existing parquet and dedupe on
        # (symbol, ts_open) — keeping the freshest version of each bar — so the
        # dataset grows over time across days instead of being overwritten.
        if path.exists() and not fresh.empty:
            try:
                prev = pd.read_parquet(path)
                before = len(prev)
                merged = pd.concat([prev, fresh], ignore_index=True)
                merged = (merged
                          .drop_duplicates(subset=["symbol", "ts_open"], keep="last")
                          .sort_values(["symbol", "ts_open"])
                          .reset_index(drop=True))
                added = len(merged) - before
                cache[tf] = merged
                print(f"  merged {tf}: {before} existing + {added} new "
                      f"= {len(merged)} rows (kept history)")
            except Exception as e:
                print(f"  [warn] merge failed ({e}); writing fresh only")
                cache[tf] = fresh
        else:
            cache[tf] = fresh
        cache[tf].to_parquet(path)
        print(f"  saved {path.name} ({len(cache[tf])} rows)")

    # analyse
    results: dict[str, dict] = {}
    for c in names:
        by_tf = {}
        for tf in TFS:
            sub = cache[tf][cache[tf]["symbol"] == c]
            m = analyse(sub, args.cost_bps)
            if not m:
                continue
            tag, trd = classify(m, args.cost_bps)
            by_tf[tf] = {"m": m, "tag": tag, "trd": trd}
        if by_tf:
            results[c] = by_tf

    # report
    lines: list[str] = []
    w = lines.append
    w("# Hyperliquid top-20 — comportement des coins & calibration\n")
    w(f"*{time.strftime('%Y-%m-%dT%H:%M:%S')} · 5000 bars max/TF · cost ref "
      f"{args.cost_bps}bps RT*\n")
    w("`typ_move` = médiane |retour close-to-close| (bps). `tradeable` = % de "
      "barres dont |move| > coût. `AC1` = autocorr lag-1 (+momentum/−reversion). "
      "`VR5` = variance ratio (>1 trend, <1 revert). `ER` = efficiency ratio "
      "(haut=directionnel). `H` = Hurst (>0.5 trend).\n")

    for tf in TFS:
        w(f"\n## Timeframe {tf}\n")
        w("| Coin | maxLev | typ_move bps | vol bps | tradeable | AC1 | VR5 | ER | H | tag |")
        w("|---|---:|---:|---:|---:|---:|---:|---:|---:|---|")
        for c in names:
            if c not in results or tf not in results[c]:
                continue
            r = results[c][tf]; m = r["m"]
            w(f"| {c} | {maxlev[c]}x | {m['typ_move_bps']:.1f} | {m['vol_bps']:.1f} | "
              f"{r['trd']} {m['tradeable_frac']*100:.0f}% | {m['ac1']:+.3f} | "
              f"{m['vr5']:.2f} | {m['eff_ratio']:.2f} | {m['hurst']:.2f} | {r['tag']} |")

    w("\n## Recommandation par coin\n")
    w("| Coin | vol24 $M | maxLev | Reco |")
    w("|---|---:|---:|---|")
    for c in names:
        if c not in results:
            continue
        reco = recommend(c, results[c], args.cost_bps, maxlev[c])
        w(f"| {c} | {vol24[c]/1e6:.0f} | {maxlev[c]}x | {reco} |")
        print(f"  {c:8s} {reco}")

    # hypotheses
    trend = [c for c in results if "1h" in results[c] and results[c]["1h"]["tag"] == "TREND"]
    revert = [c for c in results if "1h" in results[c] and results[c]["1h"]["tag"] == "REVERT"]
    untradeable = [c for c in results
                   if all(results[c][tf]["m"]["typ_move_bps"] <= args.cost_bps
                          for tf in results[c])]
    w("\n## Hypothèses\n")
    w(f"- **Trending @1h** (momentum/breakout candidats) : {', '.join(trend) or '—'}")
    w(f"- **Mean-reverting @1h** (reversion candidats) : {', '.join(revert) or '—'}")
    w(f"- **Mouvement < coût sur tous les TF** (à éviter sans maker) : {', '.join(untradeable) or '—'}")
    w("- Le `maxLev` HL plafonne le levier réalisable : BTC 40x, ETH 25x, "
      "alts 5-10x. Tout backtest au-delà est théorique.")
    w("- Plus le TF est court, plus `typ_move` rétrécit vers le coût → l'edge "
      "directionnel à 1m est presque toujours mangé par les frais ; viser 15m-1h "
      "pour le bar-trading, et la microstructure (sub-seconde) seulement en maker.")

    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
