"""
ic_quickscan.py — Quick information-coefficient scan of collected seconds features.

Reads logs/seconds_features.csv, builds forward returns per symbol at several
horizons, and computes the Spearman IC of every candidate microstructure signal
against those forward returns. Also reports the top-decile minus bottom-decile
forward-return spread (in bps) so it can be compared directly to the ~8-12 bps
round-trip cost.

This is the "is there any alpha here" smoke test from ALPHA_RESEARCH_FRAMEWORK.md
§4 and §8 — NOT a backtest. A signal with |IC| < ~0.01 or a decile spread below
the cost is not tradeable as-is.

Usage:
    python scripts/ic_quickscan.py
    python scripts/ic_quickscan.py --features logs/seconds_features.csv --out reports/ic_quickscan.md
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

# Horizons (seconds) for forward returns.
HORIZONS = [5, 15, 30, 60, 120]

# Candidate directional signals present in seconds_features.csv.
SIGNALS = [
    "obi_1", "obi_3", "obi_5", "obi_10",
    "trade_imbalance_5s", "trade_imbalance_10s", "trade_imbalance_30s",
    "microprice_pressure", "vwap_slope_5_30",
    "r_5s", "r_15s", "r_30s",
    "book_flow_alignment", "book_flow_divergence",
    "absorption_buy_proxy", "absorption_sell_proxy",
    "liquidity_vacuum", "pressure_score_raw",
]

ROUND_TRIP_COST_BPS = 10.0  # ~3-4.5 bps fee + ~2 bps slippage, per side.


def _forward_returns(df_sym: pd.DataFrame, horizon_s: int) -> np.ndarray:
    """Forward log-return mid_{t+h}/mid_t, NaN where the +h row is missing
    or the time gap is not within [h-2, h+2] s (handles logger gaps)."""
    ts = df_sym["ts"].to_numpy(dtype=float)
    mid = df_sym["mid"].to_numpy(dtype=float)
    n = len(ts)
    out = np.full(n, np.nan)
    j = 0
    for i in range(n):
        target = ts[i] + horizon_s
        if j < i:
            j = i
        while j < n and ts[j] < target - 2.0:
            j += 1
        if j < n and abs(ts[j] - target) <= 2.0 and mid[i] > 0 and mid[j] > 0:
            out[i] = np.log(mid[j] / mid[i])
    return out


def scan(features_path: str) -> str:
    p = Path(features_path)
    if not p.exists():
        return f"ERREUR: {features_path} introuvable."

    df = pd.read_csv(p)
    cols = set(df.columns)
    if "ts" not in cols or "mid" not in cols or "symbol" not in cols:
        return f"ERREUR: colonnes ts/mid/symbol manquantes. Colonnes: {sorted(cols)}"

    sigs = [s for s in SIGNALS if s in cols]
    missing = [s for s in SIGNALS if s not in cols]

    lines: list[str] = []
    lines.append("# IC Quick-Scan — signaux microstructure collectés\n")
    lines.append(f"Source : `{features_path}` — {len(df)} lignes, "
                 f"{df['symbol'].nunique()} symboles.\n")
    if missing:
        lines.append(f"Signaux absents du CSV (logger ne les écrit pas) : "
                     f"`{', '.join(missing)}`\n")
    lines.append(f"Coût round-trip de référence : **{ROUND_TRIP_COST_BPS:.0f} bps**. "
                 "Un signal n'est exploitable que si l'écart de rendement "
                 "décile-haut − décile-bas dépasse ce coût.\n")

    # Build forward returns per symbol.
    fwd: dict[int, np.ndarray] = {h: [] for h in HORIZONS}
    sig_vals: dict[str, list] = {s: [] for s in sigs}
    per_symbol_rows = []

    for sym, g in df.groupby("symbol"):
        g = g.sort_values("ts")
        if len(g) < 200:
            continue
        per_symbol_rows.append((sym, len(g)))
        for h in HORIZONS:
            fwd[h].append(_forward_returns(g, h))
        for s in sigs:
            sig_vals[s].append(pd.to_numeric(g[s], errors="coerce").to_numpy(dtype=float))

    if not per_symbol_rows:
        return "ERREUR: aucun symbole avec assez de lignes (>=200)."

    fwd = {h: np.concatenate(v) for h, v in fwd.items()}
    sig_pool = {s: np.concatenate(v) for s, v in sig_vals.items()}

    lines.append("\n## Couverture par symbole\n")
    lines.append("| Symbole | Lignes |")
    lines.append("|---|---|")
    for sym, n in sorted(per_symbol_rows, key=lambda x: -x[1]):
        lines.append(f"| {sym} | {n} |")

    # ── IC table ────────────────────────────────────────────────────────
    lines.append("\n## Spearman IC — signal vs forward return\n")
    header = "| Signal | " + " | ".join(f"IC {h}s" for h in HORIZONS) + " | n |"
    lines.append(header)
    lines.append("|" + "---|" * (len(HORIZONS) + 2))

    ic_table: dict[str, dict[int, float]] = {}
    for s in sigs:
        sv = sig_pool[s]
        row_ic = {}
        n_used = 0
        cells = []
        for h in HORIZONS:
            fv = fwd[h]
            mask = np.isfinite(sv) & np.isfinite(fv)
            if mask.sum() < 200 or np.nanstd(sv[mask]) < 1e-12:
                cells.append("—")
                row_ic[h] = float("nan")
                continue
            ic, _ = spearmanr(sv[mask], fv[mask])
            row_ic[h] = ic
            n_used = max(n_used, int(mask.sum()))
            flag = " *" if abs(ic) >= 0.02 else ""
            cells.append(f"{ic:+.4f}{flag}")
        ic_table[s] = row_ic
        lines.append(f"| {s} | " + " | ".join(cells) + f" | {n_used} |")

    lines.append("\n`*` = |IC| ≥ 0.02 (seuil indicatif du framework §8). "
                 "Un IC qui change de signe selon l'horizon = bruit.\n")

    # ── Decile spread (tradeability vs cost) ────────────────────────────
    lines.append("\n## Écart de rendement décile-haut − décile-bas (bps)\n")
    lines.append("Pour l'horizon 30 s. Si l'écart < coût round-trip, le signal "
                 "ne paie pas même avec un timing parfait.\n")
    lines.append("| Signal | Décile bas | Décile haut | Écart (bps) | > coût ? |")
    lines.append("|---|---|---|---|---|")
    h = 30 if 30 in HORIZONS else HORIZONS[0]
    fv = fwd[h]
    verdicts = []
    for s in sigs:
        sv = sig_pool[s]
        mask = np.isfinite(sv) & np.isfinite(fv)
        if mask.sum() < 500 or np.nanstd(sv[mask]) < 1e-12:
            lines.append(f"| {s} | — | — | — | — |")
            continue
        svm, fvm = sv[mask], fv[mask]
        q_lo, q_hi = np.quantile(svm, 0.10), np.quantile(svm, 0.90)
        lo_ret = fvm[svm <= q_lo].mean() * 1e4
        hi_ret = fvm[svm >= q_hi].mean() * 1e4
        spread = hi_ret - lo_ret
        ok = abs(spread) > ROUND_TRIP_COST_BPS
        verdicts.append((s, spread, ok))
        lines.append(f"| {s} | {lo_ret:+.2f} | {hi_ret:+.2f} | "
                     f"{spread:+.2f} | {'OUI' if ok else 'non'} |")

    # ── Verdict ─────────────────────────────────────────────────────────
    lines.append("\n## Verdict\n")
    tradeable = [v for v in verdicts if v[2]]
    if tradeable:
        lines.append("Signaux dont l'écart de décile dépasse le coût (candidats "
                      "à creuser dans le notebook, AVEC walk-forward et retrait "
                      "du bêta-BTC) :\n")
        for s, spread, _ in sorted(tradeable, key=lambda x: -abs(x[1])):
            lines.append(f"- **{s}** : écart {spread:+.2f} bps")
    else:
        lines.append("**Aucun signal microstructure n'a un écart de décile "
                      "supérieur au coût round-trip.** Sur ces données et à ces "
                      "horizons, aucun de ces signaux n'est exploitable en taker. "
                      "C'est cohérent avec le framework §2 : OBI / pressure / "
                      "imbalance sont des mesures de liquidité, pas des alphas.")
    lines.append("\n> Rappel : un IC positif ne suffit pas. Le signal doit aussi "
                 "survivre au walk-forward, au retrait du bêta-BTC et tenir sur "
                 "plusieurs symboles (framework §6-§8).\n")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", default="logs/seconds_features.csv")
    ap.add_argument("--out", default="reports/ic_quickscan.md")
    args = ap.parse_args()

    report = scan(args.features)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    # Windows consoles default to cp1252 and choke on the report's unicode.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        print(report)
    except UnicodeEncodeError:
        print(report.encode("ascii", "replace").decode("ascii"))
    print(f"\n[ic_quickscan] rapport ecrit -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
