"""
verify_data.py — vérifie que les données sont bien récupérées ET fiables, pour le
backtest comme pour le paper trading. Trois contrôles :

  1. HISTORIQUE (data/historical/*.parquet) : nb barres, plage, gaps, doublons,
     prix ≤ 0, cohérence OHLC (high≥low, low≤close≤high), ts strictement croissant.
  2. CROISÉ multi-exchange (datafetcher klines BTC/ETH/SOL sur binance/okx/bitget) :
     les prix concordent-ils entre exchanges ? Un écart médian > tol = source suspecte
     (donnée potentiellement corrompue/mauvais symbole).
  3. FEED LIVE (runtime/data_feed_status.json) : fraîcheur par symbole (âge du
     dernier tick), bid<ask, spread sain → le paper reflète-t-il un flux vivant ?

Sortie : reports/data_verification.md + verdict console OK/WARN/FAIL par source.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.historical_data import quality_check, INTERVAL_MS   # noqa: E402

HIST = ROOT / "data" / "historical"
KLINES = Path(r"C:/Users/jeanb/Documents/Mercantour/datafetcher/klines")
FEED = ROOT / "runtime" / "data_feed_status.json"
REPORT = ROOT / "reports" / "data_verification.md"

CROSS_TOL_PCT = 0.5       # écart médian inter-exchange acceptable (%)
FEED_STALE_S = 30.0       # un tick plus vieux que ça = périmé


def check_historical() -> list[dict]:
    rows = []
    for p in sorted(HIST.glob("*.parquet")):
        name = p.stem
        if name.endswith("_funding"):
            continue
        try:
            itv = name.split("_")[-1]
            df = pd.read_parquet(p)
        except Exception as e:
            rows.append({"file": name, "ok": False, "why": f"lecture: {e}"}); continue
        if itv not in INTERVAL_MS or "ts" not in df.columns:
            rows.append({"file": name, "ok": False, "why": "format inattendu"}); continue
        q = quality_check(df, itv)
        ts = df["ts"].to_numpy()
        mono = bool(np.all(np.diff(ts) > 0))
        ohlc_bad = int(((df["high"] < df["low"]) |
                        (df["close"] > df["high"]) | (df["close"] < df["low"])).sum())
        ok = q.get("ok") and mono and ohlc_bad == 0
        rows.append({"file": name, "ok": ok, "n": q.get("n_bars"),
                     "span_j": q.get("span_days"), "gaps": q.get("n_gaps"),
                     "dups": q.get("duplicates"), "bad_px": q.get("bad_prices"),
                     "ohlc_bad": ohlc_bad, "mono": mono,
                     "cov%": q.get("coverage_pct")})
    return rows


def check_cross_source(coins=("BTC", "ETH", "SOL"),
                       exchanges=("binance", "okx", "bitget"), n=2000) -> list[dict]:
    rows = []
    for c in coins:
        series = {}
        for ex in exchanges:
            f = KLINES / ex / f"{c}_USDT_1m.parquet"
            if f.exists():
                try:
                    d = pd.read_parquet(f, columns=["datetime", "close"]).tail(n)
                    series[ex] = d.set_index("datetime")["close"]
                except Exception:
                    pass
        if len(series) < 2:
            rows.append({"coin": c, "ok": None, "why": f"{len(series)} source(s)"}); continue
        m = pd.DataFrame(series).dropna()
        if len(m) < 50:
            rows.append({"coin": c, "ok": None, "why": "peu de recouvrement"}); continue
        ref = m.median(axis=1)
        dev = {ex: float((np.abs(m[ex] - ref) / ref).median() * 100) for ex in m.columns}
        worst = max(dev.values())
        rows.append({"coin": c, "ok": worst <= CROSS_TOL_PCT, "overlap": len(m),
                     "dev_pct": {k: round(v, 4) for k, v in dev.items()}, "worst_pct": round(worst, 4)})
    return rows


def check_live_feed() -> dict:
    if not FEED.exists():
        return {"ok": None, "why": "feed absent (moteur non lancé ?)"}
    try:
        d = json.loads(FEED.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "why": f"lecture: {e}"}
    now = time.time()
    feed_age = now - float(d.get("ts", 0))
    sf = d.get("seconds_features", {}) or {}
    per = []
    fresh = stale = bad = 0
    dead = []
    for sym, v in sf.items():
        if not isinstance(v, dict):
            continue
        age = now - float(v.get("ts", 0) or 0)
        bid, ask = v.get("best_bid"), v.get("best_ask")
        sane = bool(bid and ask and bid > 0 and ask >= bid)
        is_fresh = age <= FEED_STALE_S and sane
        fresh += is_fresh; stale += (age > FEED_STALE_S); bad += (not sane)
        if not sane:
            dead.append(sym)                      # coin sans carnet (illiquide/délisté)
        per.append({"sym": sym, "age_s": round(age, 1), "spread_bps": v.get("spread_bps"), "sane": sane})
    n = len(per)
    # Sain = statut frais, AUCUN symbole périmé, et ≥80% des coins cotés. Un coin
    # sans carnet (ex. BLAST) est signalé mais ne condamne pas tout le feed.
    ok = (feed_age <= FEED_STALE_S and stale == 0 and n > 0 and fresh >= max(1, int(0.8 * n)))
    return {"ok": ok, "feed_age_s": round(feed_age, 1), "n_symbols": n,
            "fresh": fresh, "stale": stale, "bad": bad, "dead_symbols": dead,
            "running": (d.get("data_feed_health", {}) or {}).get("running"),
            "per": sorted(per, key=lambda x: -x["age_s"])[:8]}


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    hist = check_historical()
    cross = check_cross_source()
    live = check_live_feed()

    L = ["# Vérification de fiabilité des données\n",
         f"*{time.strftime('%Y-%m-%dT%H:%M:%S')}*\n",
         "## 1. Historique (backtest)\n",
         "| Fichier | OK | Barres | Span j | Gaps | Dups | Prix≤0 | OHLC incohérent | ts croissant | Couv % |",
         "|---|:--:|---:|---:|---:|---:|---:|---:|:--:|---:|"]
    for r in hist:
        if "n" not in r:
            L.append(f"| {r['file']} | ❌ | — | — | — | — | — | — | — | {r.get('why','')} |"); continue
        L.append(f"| {r['file']} | {'✅' if r['ok'] else '⚠️'} | {r['n']} | {r['span_j']} | "
                 f"{r['gaps']} | {r['dups']} | {r['bad_px']} | {r['ohlc_bad']} | "
                 f"{'oui' if r['mono'] else 'NON'} | {r['cov%']} |")
    n_ok = sum(1 for r in hist if r.get("ok"))
    L.append(f"\n→ {n_ok}/{len(hist)} fichiers historiques OK\n")

    L.append("## 2. Concordance inter-exchange (fiabilité prix)\n")
    L.append("| Coin | OK | Recouvrement | Écart médian par exchange (%) | Pire |")
    L.append("|---|:--:|---:|---|---:|")
    for r in cross:
        if r.get("ok") is None:
            L.append(f"| {r['coin']} | — | — | {r.get('why','')} | — |"); continue
        L.append(f"| {r['coin']} | {'✅' if r['ok'] else '⚠️'} | {r['overlap']} | "
                 f"{r['dev_pct']} | {r['worst_pct']}% |")
    L.append(f"\n(seuil concordance : ≤ {CROSS_TOL_PCT}% d'écart médian)\n")

    L.append("## 3. Feed live (paper trading)\n")
    if live.get("ok") is None:
        L.append(f"_{live.get('why')}_\n")
    else:
        L.append(f"- Verdict : {'✅ feed sain' if live['ok'] else '⚠️ feed dégradé'}")
        L.append(f"- Âge du statut : {live['feed_age_s']}s · symboles {live['n_symbols']} · "
                 f"frais {live['fresh']} / périmés {live['stale']} / sans carnet {live['bad']} · "
                 f"running={live.get('running')}")
        if live.get("dead_symbols"):
            L.append(f"- ⚠️ Sans carnet (illiquide/délisté HL — à retirer de l'univers) : "
                     f"{', '.join(live['dead_symbols'])}")
        L.append(f"- (seuil fraîcheur : ≤ {FEED_STALE_S:.0f}s)\n")
        L.append("| Symbole | Âge tick (s) | Spread bps | Sain |\n|---|---:|---:|:--:|")
        for p in live["per"]:
            L.append(f"| {p['sym']} | {p['age_s']} | {p['spread_bps']} | {'✅' if p['sane'] else '❌'} |")

    REPORT.write_text("\n".join(L), encoding="utf-8")
    # console
    print(f"HISTORIQUE : {n_ok}/{len(hist)} fichiers OK")
    cross_ok = sum(1 for r in cross if r.get('ok'))
    print(f"INTER-EXCHANGE : {cross_ok}/{sum(1 for r in cross if r.get('ok') is not None)} coins concordants",
          {r['coin']: r.get('worst_pct') for r in cross if r.get('ok') is not None})
    if live.get("ok") is None:
        print(f"FEED LIVE : {live.get('why')}")
    else:
        print(f"FEED LIVE : {'OK' if live['ok'] else 'DÉGRADÉ'} — {live['fresh']} frais / "
              f"{live['stale']} périmés / {live['bad']} sans carnet (âge statut {live['feed_age_s']}s)"
              + (f" · sans carnet: {', '.join(live['dead_symbols'])}" if live.get('dead_symbols') else ""))
    print(f"Rapport -> {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
