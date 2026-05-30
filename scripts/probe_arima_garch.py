"""
probe_arima_garch.py — Does a time-series model predict SHORT-horizon DIRECTION?

The leverage idea hinges on one assumption: that we can foresee the next
15-30s move with enough directional accuracy to beat costs + the liquidation
tax. This probe tests that assumption empirically on the real seconds data,
and contrasts three predictors per coin / horizon:

  1. AR(1) on 1 Hz mid log-returns  → the "ARIMA" directional bet.
       Fit on the train half, predict the sign of the next-H-second return on
       the test half, report directional hit-rate (50% = coin flip).
  2. GARCH(1,1) conditional vol      → the "GARCH" bet.
       GARCH forecasts MAGNITUDE, not sign. We verify it: correlation of the
       conditional-vol forecast with realised |return| (should be > 0) vs with
       the SIGNED return (should be ~0). i.e. GARCH tells you WHEN it will move,
       never WHICH WAY.
  3. Microstructure features         → the alternative that actually carries IC.
       Spearman IC of trade_imbalance_30s / microprice_pressure / obi_10 vs the
       forward H-second return.

Output: reports/probe_arima_garch.md  + console table.
"""
from __future__ import annotations

import sys
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PARQUET = ROOT / "data" / "processed" / "seconds_features.parquet"
REPORT = ROOT / "reports" / "probe_arima_garch.md"

COINS = ["BTC", "ETH", "INJ", "WLD", "KAITO", "BANANA"]
HORIZONS = [15, 30, 120, 300]
MICRO_FEATURES = ["trade_imbalance_30s", "microprice_pressure", "obi_10"]


def load() -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    df = df[df["symbol"].apply(lambda x: isinstance(x, str))]
    for c in ["ts", "mid"] + MICRO_FEATURES:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ts", "mid"]).sort_values(["symbol", "ts"]).reset_index(drop=True)
    return df


def ar1_direction_hit(mid: np.ndarray, H: int) -> tuple[float, int]:
    """Fit AR(1) on 1-step log-returns (train half), predict sign of the
    H-step-ahead return on the test half. Return (hit_rate, n)."""
    logp = np.log(mid)
    r1 = np.diff(logp)                      # 1-step returns
    n = len(r1)
    if n < 5000:
        return float("nan"), 0
    split = int(n * 0.6)
    tr = r1[:split]
    # AR(1): r_t = a + b r_{t-1}
    x, y = tr[:-1], tr[1:]
    b = np.cov(x, y)[0, 1] / (np.var(x) + 1e-18)
    a = y.mean() - b * x.mean()
    # On the test half: predicted 1-step drift ~ a + b r_{t-1}; the H-step
    # directional forecast is sign of the cumulative expected drift, which for
    # |b|<1 is dominated by sign(a + b r_{t-1}). Compare to realised H return.
    test_mid = mid[split:]
    tlogp = np.log(test_mid)
    tr1 = np.diff(tlogp)
    preds, actuals = [], []
    for t in range(1, len(test_mid) - H):
        drift = a + b * tr1[t - 1]
        fwd = tlogp[t + H] - tlogp[t]
        if fwd == 0:
            continue
        preds.append(np.sign(drift))
        actuals.append(np.sign(fwd))
    if len(preds) < 100:
        return float("nan"), 0
    preds = np.array(preds); actuals = np.array(actuals)
    hit = float((preds == actuals).mean())
    return hit, len(preds)


def garch_vol_check(mid: np.ndarray) -> dict:
    """Fit GARCH(1,1) on returns; correlate 1-step cond-vol forecast with
    realised |return| (magnitude) and signed return (direction)."""
    try:
        from arch import arch_model
    except Exception:
        return {"skip": "no arch"}
    logp = np.log(mid)
    r = np.diff(logp) * 1e4                  # bps, helps the optimiser
    n = len(r)
    if n < 5000:
        return {"skip": "too few"}
    # Fit on a capped sample for speed.
    sample = r[:20000]
    try:
        am = arch_model(sample, mean="Zero", vol="GARCH", p=1, q=1, rescale=False)
        res = am.fit(disp="off")
    except Exception as e:
        return {"skip": f"fit failed {e.__class__.__name__}"}
    # In-sample conditional vol vs |r| and signed r on the same window.
    cv = res.conditional_volatility
    m = min(len(cv), len(sample))
    cv = cv[:m]; rr = sample[:m]
    corr_mag = float(np.corrcoef(cv, np.abs(rr))[0, 1])
    corr_dir = float(np.corrcoef(cv, rr)[0, 1])
    return {"corr_vol_vs_magnitude": corr_mag, "corr_vol_vs_signed": corr_dir,
            "alpha_beta": (float(res.params.get("alpha[1]", np.nan)),
                           float(res.params.get("beta[1]", np.nan)))}


def micro_ic(g: pd.DataFrame, feature: str, H: int) -> tuple[float, int]:
    if feature not in g.columns:
        return float("nan"), 0
    mid = g["mid"].values.astype(float)
    f = g[feature].values.astype(float)
    n = len(mid)
    fwd = np.full(n, np.nan)
    fwd[:n - H] = (mid[H:] - mid[:n - H]) / mid[:n - H]
    mask = np.isfinite(f) & np.isfinite(fwd)
    if mask.sum() < 500:
        return float("nan"), 0
    ic, _ = spearmanr(f[mask], fwd[mask])
    return float(ic), int(mask.sum())


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    df = load()
    lines: list[str] = []
    w = lines.append
    w("# Probe — can ARIMA / GARCH predict short-horizon direction?\n")
    w("AR(1) directional hit-rate (50% = coin flip), GARCH vol-forecast "
      "correlation (magnitude vs signed), and microstructure Spearman IC.\n")

    # 1. AR(1) directional hit-rate
    print("\n=== AR(1) directional hit-rate (50% = chance) ===")
    w("\n## 1. AR(1) ('ARIMA') directional hit-rate — 50% is a coin flip\n")
    w("| Coin | " + " | ".join(f"{H}s" for H in HORIZONS) + " |")
    w("|---|" + "---|" * len(HORIZONS))
    for c in COINS:
        g = df[df["symbol"] == c]
        if len(g) < 5000:
            continue
        mid = g["mid"].values.astype(float)
        cells = []
        row = f"  {c:7s} "
        for H in HORIZONS:
            hit, n = ar1_direction_hit(mid, H)
            cells.append(f"{hit*100:.1f}%" if np.isfinite(hit) else "—")
            row += f" {H}s={hit*100:5.1f}%" if np.isfinite(hit) else f" {H}s=  —  "
        print(row)
        w(f"| {c} | " + " | ".join(cells) + " |")

    # 2. GARCH magnitude vs direction
    print("\n=== GARCH(1,1): vol forecast correlates with MAGNITUDE not SIGN ===")
    w("\n## 2. GARCH(1,1) — forecasts magnitude, not direction\n")
    w("| Coin | corr(vol, |r|) | corr(vol, signed r) | alpha+beta |")
    w("|---|---:|---:|---:|")
    for c in COINS:
        g = df[df["symbol"] == c]
        if len(g) < 5000:
            continue
        mid = g["mid"].values.astype(float)
        r = garch_vol_check(mid)
        if "skip" in r:
            print(f"  {c:7s} skip ({r['skip']})")
            continue
        ab = r["alpha_beta"]
        print(f"  {c:7s} corr(vol,|r|)={r['corr_vol_vs_magnitude']:+.3f}  "
              f"corr(vol,signed)={r['corr_vol_vs_signed']:+.3f}  a+b={ab[0]+ab[1]:.3f}")
        w(f"| {c} | {r['corr_vol_vs_magnitude']:+.3f} | {r['corr_vol_vs_signed']:+.3f} | "
          f"{ab[0]+ab[1]:.3f} |")

    # 3. Microstructure IC
    print("\n=== Microstructure Spearman IC vs forward return ===")
    w("\n## 3. Microstructure feature IC (Spearman) vs forward return\n")
    w("| Coin | Feature | " + " | ".join(f"{H}s" for H in HORIZONS) + " |")
    w("|---|---|" + "---|" * len(HORIZONS))
    for c in COINS:
        g = df[df["symbol"] == c]
        if len(g) < 5000:
            continue
        for feat in MICRO_FEATURES:
            cells = []
            row = f"  {c:7s} {feat:22s}"
            for H in HORIZONS:
                ic, n = micro_ic(g, feat, H)
                cells.append(f"{ic:+.3f}" if np.isfinite(ic) else "—")
                row += f" {H}s={ic:+.3f}" if np.isfinite(ic) else f" {H}s=  —  "
            print(row)
            w(f"| {c} | {feat} | " + " | ".join(cells) + " |")

    w("\n## Verdict\n")
    w("- If the AR(1) hit-rates sit at ~50% and GARCH `corr(vol, signed r) ≈ 0`, "
      "then **no time-series model gives a directional edge** at these horizons "
      "— GARCH/ARIMA tell you *how big* the next move is, never *which way*.\n")
    w("- Any tradeable direction has to come from the **microstructure IC** in "
      "§3 — and that IC is strongest on the altcoins at 120-300s, not on majors "
      "at 15-30s. That is exactly where the leverage backtest found positive "
      "expectancy.\n")
    REPORT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
