"""
alpha_research_workflow.py — Full alpha-research pass on collected
seconds_features.csv data.

Goes beyond `ic_quickscan.py` by adding the **two tests that actually
distinguish alpha from beta** (per `docs/ALPHA_RESEARCH_FRAMEWORK.md`) :

  1. **BTC-residualised IC.** A signal that predicts asset returns might
     just be a BTC-beta proxy (price goes up → all alts go up). We regress
     each asset's forward return on BTC's same-window return, subtract the
     beta component, and re-compute the IC against the residual. If the
     residual IC collapses, the signal is beta dressed up as alpha.

  2. **Walk-forward IC.** Splits the time axis into K windows. A real
     edge holds across windows (consistent sign, mean > 0, std < |mean|).
     A signal whose IC flips sign across windows is overfitting noise.

Output: `reports/alpha_research_report.md`. Verdict per (signal, horizon)
is one of:
    candidate       — passes residual + walk-forward + cost-adjusted
    beta_proxy      — strong raw IC, residual IC ~0
    sub_cost        — IC ok but the decile spread doesn't beat round-trip cost
    unstable        — walk-forward IC flips sign
    weak            — |IC| < 0.01 — noise

Usage from the repo root:
    python scripts/alpha_research_workflow.py
    python scripts/alpha_research_workflow.py --features logs/seconds_features.csv
    python scripts/alpha_research_workflow.py --cost-bps 8 --horizons 60,300,900
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# Horizons (seconds) for forward returns. Bar-level (60s+) is where bar
# strategies and momentum live; the sub-60s horizons are for scalpers.
DEFAULT_HORIZONS = (60, 300, 900, 1800)
DEFAULT_COST_BPS = 10.0
DEFAULT_WF_FOLDS = 5

SIGNALS = [
    "obi_1", "obi_3", "obi_5", "obi_10",
    "trade_imbalance_5s", "trade_imbalance_10s", "trade_imbalance_30s",
    "microprice_pressure", "vwap_slope_5_30",
    "r_5s", "r_15s", "r_30s",
    "book_flow_alignment", "book_flow_divergence",
    "absorption_buy_proxy", "absorption_sell_proxy",
    "liquidity_vacuum", "pressure_score_raw",
]


# ─── Forward returns + BTC-residualisation ──────────────────────────────────

def _forward_returns(df_sym: pd.DataFrame, horizon_s: int) -> np.ndarray:
    """Log-return mid_{t+h}/mid_t, NaN where the +h sample is missing OR the
    time gap is not within ±5 s of the target (handles logger gaps)."""
    ts = df_sym["ts"].to_numpy(dtype=float)
    mid = df_sym["mid"].to_numpy(dtype=float)
    n = len(ts)
    out = np.full(n, np.nan)
    j = 0
    for i in range(n):
        target = ts[i] + horizon_s
        if j < i:
            j = i
        while j < n and ts[j] < target - 5.0:
            j += 1
        if j < n and abs(ts[j] - target) <= 5.0 and mid[i] > 0 and mid[j] > 0:
            out[i] = np.log(mid[j] / mid[i])
    return out


def _btc_residualise(asset_ret: np.ndarray, btc_ret: np.ndarray
                     ) -> tuple[np.ndarray, float]:
    """Return (residual_returns, beta). Residual = asset - beta * btc.
    Beta = cov(asset, btc) / var(btc), simple OLS without intercept (the
    forward returns are already near-zero-centred over the window)."""
    mask = np.isfinite(asset_ret) & np.isfinite(btc_ret)
    if mask.sum() < 50:
        return np.full_like(asset_ret, np.nan), float("nan")
    a, b = asset_ret[mask], btc_ret[mask]
    var_b = np.var(b)
    if var_b < 1e-18:
        return asset_ret.copy(), 0.0
    beta = float(np.cov(a, b, ddof=0)[0, 1] / var_b)
    resid = asset_ret - beta * btc_ret
    return resid, beta


# ─── IC + walk-forward ──────────────────────────────────────────────────────

def _ic(sig: np.ndarray, ret: np.ndarray) -> tuple[float, int]:
    mask = np.isfinite(sig) & np.isfinite(ret)
    n = int(mask.sum())
    if n < 200 or np.nanstd(sig[mask]) < 1e-12:
        return float("nan"), n
    ic, _ = spearmanr(sig[mask], ret[mask])
    return float(ic), n


def _walk_forward_ic(sig: np.ndarray, ret: np.ndarray, folds: int
                     ) -> tuple[float, float, list[float]]:
    """Equal-length splits along the index axis. Return (mean_IC, std_IC,
    per_fold_IC). Same-sign across folds = stable; sign flips = unstable."""
    ics: list[float] = []
    n = len(sig)
    if n < folds * 200:
        return float("nan"), float("nan"), ics
    edges = np.linspace(0, n, folds + 1, dtype=int)
    for k in range(folds):
        s = sig[edges[k]:edges[k + 1]]
        r = ret[edges[k]:edges[k + 1]]
        ic, _ = _ic(s, r)
        if np.isfinite(ic):
            ics.append(ic)
    if not ics:
        return float("nan"), float("nan"), ics
    return float(np.mean(ics)), float(np.std(ics)), ics


def _decile_spread_bps(sig: np.ndarray, ret: np.ndarray) -> float:
    """Top-decile − bottom-decile mean forward return, in bps."""
    mask = np.isfinite(sig) & np.isfinite(ret)
    if mask.sum() < 500 or np.nanstd(sig[mask]) < 1e-12:
        return float("nan")
    s, r = sig[mask], ret[mask]
    q_lo, q_hi = np.quantile(s, 0.10), np.quantile(s, 0.90)
    return float((r[s >= q_hi].mean() - r[s <= q_lo].mean()) * 1e4)


# ─── Per-signal evaluation ──────────────────────────────────────────────────

def _verdict(raw_ic: float, resid_ic: float, decile_bps: float,
             wf_mean: float, wf_std: float, cost_bps: float) -> str:
    if not np.isfinite(raw_ic) or abs(raw_ic) < 0.01:
        return "weak"
    if np.isfinite(resid_ic) and abs(resid_ic) < 0.005 and abs(raw_ic) >= 0.03:
        return "beta_proxy"
    if np.isfinite(decile_bps) and abs(decile_bps) < cost_bps:
        return "sub_cost"
    if (np.isfinite(wf_mean) and np.isfinite(wf_std)
            and wf_std > abs(wf_mean) * 1.5):
        return "unstable"
    if (np.isfinite(resid_ic) and abs(resid_ic) >= 0.015
            and np.isfinite(decile_bps) and abs(decile_bps) > cost_bps):
        return "candidate"
    return "weak"


# ─── Main report ────────────────────────────────────────────────────────────

def run(features_path: str, horizons: tuple[int, ...], cost_bps: float,
        wf_folds: int, out_path: str) -> int:
    p = Path(features_path)
    if not p.exists():
        print(f"ERROR: {features_path} introuvable.")
        return 1
    # Canonical header for the seconds-features logger. Used as a fallback
    # when the live file lost its header (artefact of a restart while a
    # logger had the file open and the restart didn't rewrite the header).
    _CANON = [
        "ts", "datetime", "symbol", "mid", "best_bid", "best_ask",
        "spread_bps", "obi_1", "obi_3", "obi_5", "obi_10",
        "trade_imbalance_5s", "trade_imbalance_10s", "trade_imbalance_30s",
        "buy_volume_usd_10s", "sell_volume_usd_10s",
        "vwap_5s", "vwap_15s", "vwap_30s", "vwap_slope_5_30",
        "microprice", "microprice_pressure",
        "r_5s", "r_15s", "r_30s", "rv_30s", "rv_60s",
        "book_flow_alignment", "book_flow_divergence",
        "absorption_sell_proxy", "absorption_buy_proxy",
        "liquidity_vacuum", "pressure_score_raw",
        "book_stale", "enough_data",
    ]
    df = pd.read_csv(p)
    cols = set(df.columns)
    needed = {"ts", "symbol", "mid"}
    if not needed <= cols:
        # Try header-less mode — falls back if the first row looks like data.
        first = list(df.columns)
        looks_like_data = any(_is_numeric(c) for c in first[:3])
        if looks_like_data:
            print("INFO: first row looks like data — re-reading without header.")
            df = pd.read_csv(p, header=None, names=_CANON[:len(first)])
            cols = set(df.columns)
        if not needed <= cols:
            print(f"ERROR: missing required columns. Got {sorted(cols)[:10]}…")
            return 1
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    df["mid"] = pd.to_numeric(df["mid"], errors="coerce")
    df = df.dropna(subset=["ts", "mid", "symbol"]).copy()
    sigs = [s for s in SIGNALS if s in cols]
    missing = [s for s in SIGNALS if s not in cols]

    syms = sorted(df["symbol"].unique())
    if "BTC" not in syms:
        print("ERROR: no BTC rows — BTC is needed for the residualisation.")
        return 1

    lines: list[str] = []
    lines.append("# Alpha Research Workflow — full report\n")
    lines.append(f"Source : `{features_path}` — {len(df)} lignes, "
                 f"{len(syms)} symboles.\n")
    if missing:
        lines.append(f"Signaux absents du CSV : `{', '.join(missing)}`\n")
    lines.append(f"Coût round-trip de référence : **{cost_bps:.0f} bps**. "
                 f"Walk-forward : **{wf_folds} folds**. "
                 f"Horizons : {', '.join(str(h) + 's' for h in horizons)}.\n")

    # Build forward returns per horizon, pooling assets (residual vs BTC).
    grouped = {sym: g.sort_values("ts").reset_index(drop=True)
               for sym, g in df.groupby("symbol")}
    btc = grouped["BTC"]

    # For each horizon, build raw + residual forward returns, pooled across
    # symbols. Signals are pooled in the same order.
    for h in horizons:
        lines.append(f"\n## Horizon {h}s\n")
        raw_ret_pool: list[np.ndarray] = []
        res_ret_pool: list[np.ndarray] = []
        sig_pool: dict[str, list[np.ndarray]] = {s: [] for s in sigs}
        per_sym_beta: dict[str, float] = {}

        btc_ret_full = _forward_returns(btc, h)

        for sym, g in grouped.items():
            if len(g) < 200:
                continue
            ret = _forward_returns(g, h)
            if sym == "BTC":
                # Residualising BTC against itself yields zero; skip BTC from the
                # residual pool but keep it in the raw pool.
                resid = np.full_like(ret, np.nan)
                per_sym_beta[sym] = 1.0
            else:
                # Align by row index (both arrays come from the same per-sym
                # frame iteration; for residualisation we need a BTC return
                # series of the same length aligned by timestamp).
                # Simple approach: re-build BTC return at each ts in this sym.
                btc_ret = np.interp(g["ts"].to_numpy(dtype=float),
                                     btc["ts"].to_numpy(dtype=float),
                                     btc_ret_full, left=np.nan, right=np.nan)
                resid, beta = _btc_residualise(ret, btc_ret)
                per_sym_beta[sym] = beta
            raw_ret_pool.append(ret)
            res_ret_pool.append(resid)
            for s in sigs:
                v = pd.to_numeric(g[s], errors="coerce").to_numpy(dtype=float)
                sig_pool[s].append(v)

        if not raw_ret_pool:
            lines.append("*pas assez de données pour cet horizon*")
            continue
        raw_ret = np.concatenate(raw_ret_pool)
        res_ret = np.concatenate(res_ret_pool)
        pooled_sig = {s: np.concatenate(v) for s, v in sig_pool.items()}

        # Header
        lines.append("| Signal | IC raw | IC résiduel | Δ décile (bps) | "
                     "Δ - coût | WF mean ± std | Verdict |")
        lines.append("|---|---|---|---|---|---|---|")
        for s in sigs:
            v = pooled_sig[s]
            raw_ic, n = _ic(v, raw_ret)
            res_ic, _ = _ic(v, res_ret)
            spread = _decile_spread_bps(v, raw_ret)
            net = (spread - cost_bps) if np.isfinite(spread) else float("nan")
            wf_mean, wf_std, _ = _walk_forward_ic(v, raw_ret, wf_folds)
            v_verdict = _verdict(raw_ic, res_ic, spread, wf_mean, wf_std, cost_bps)
            flag = {
                "candidate":  "**CANDIDATE**",
                "beta_proxy": "beta_proxy",
                "sub_cost":   "sub_cost",
                "unstable":   "unstable",
                "weak":       "weak",
            }[v_verdict]
            lines.append(
                f"| `{s}` | {_fmt(raw_ic, '+.4f')} | {_fmt(res_ic, '+.4f')} | "
                f"{_fmt(spread, '+.2f')} | {_fmt(net, '+.2f')} | "
                f"{_fmt(wf_mean, '+.4f')} ± {_fmt(wf_std, '.4f')} | {flag} |"
            )

        # Per-symbol betas for this horizon (useful sanity).
        b_lines = ", ".join(f"{k}={v:+.2f}" for k, v in per_sym_beta.items()
                            if k != "BTC" and np.isfinite(v))
        lines.append(f"\n*betas (asset vs BTC, this horizon)* — {b_lines}\n")

    # ─── Process summary / glossary ────────────────────────────────────────
    lines.append("\n---\n## Comment lire ce rapport\n")
    lines.append(
        "**Le test qui compte** est `IC résiduel` : on enlève le bêta BTC et\n"
        "on regarde si le signal prédit encore. S'il s'effondre vs `IC raw`,\n"
        "le signal n'est qu'un proxy du mouvement BTC — acheter BTC via cet\n"
        "alt produit le même rendement sans le signal.\n\n"
        "**`Δ décile (bps)`** = écart de rendement entre les 10% top-signal\n"
        "et les 10% bottom-signal sur cet horizon. **`Δ - coût`** doit être\n"
        "strictement positif pour qu'un trade taker soit rentable.\n\n"
        "**`WF mean ± std`** = IC moyen sur K fenêtres temporelles. Si\n"
        "`std > 1.5 × |mean|`, l'edge est instable (signe qui flip selon la\n"
        "période).\n\n"
        "**Verdict** :\n"
        "- `CANDIDATE` : passe résiduel + walk-forward + coût → à creuser.\n"
        "- `beta_proxy` : IC raw fort, IC résiduel ~0 → ce n'est pas de l'alpha.\n"
        "- `sub_cost` : prédictif mais l'amplitude ne couvre pas le coût.\n"
        "- `unstable` : IC flip de signe selon la période, surfit probable.\n"
        "- `weak` : |IC| < 0.01, signal au niveau du bruit.\n\n"
        "*Ne jamais activer une stratégie sur un signal qui n'est pas\n"
        "CANDIDATE sur au moins 2 horizons cohérents.*"
    )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        print("\n".join(lines))
    except Exception:
        pass
    print(f"\n[alpha_research] rapport ecrit -> {out}")
    return 0


def _fmt(x: float, spec: str) -> str:
    if not isinstance(x, (int, float)) or not np.isfinite(x):
        return "—"
    return format(x, spec)


def _is_numeric(s: str) -> bool:
    try:
        float(s)
        return True
    except (TypeError, ValueError):
        return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", default="logs/seconds_features.csv")
    ap.add_argument("--out",      default="reports/alpha_research_report.md")
    ap.add_argument("--cost-bps", type=float, default=DEFAULT_COST_BPS)
    ap.add_argument("--folds",    type=int, default=DEFAULT_WF_FOLDS)
    ap.add_argument("--horizons", default=",".join(str(h) for h in DEFAULT_HORIZONS),
                    help="Comma-separated horizons in seconds.")
    args = ap.parse_args()
    horizons = tuple(int(x.strip()) for x in args.horizons.split(",") if x.strip())
    return run(args.features, horizons, args.cost_bps, args.folds, args.out)


if __name__ == "__main__":
    sys.exit(main())
