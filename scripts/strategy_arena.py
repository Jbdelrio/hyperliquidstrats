"""
strategy_arena.py — comparateur & sélecteur de stratégies (méta-niveau).

But : faire tourner TOUTES les stratégies, les comparer honnêtement, et désigner
celle(s) éligible(s) au LIVE — sous la contrainte 500 $/strat. Adaptatif : se
recalcule à chaque exécution au fur et à mesure que la data live s'accumule.

Règle d'éligibilité LIVE (les DEUX requis, anti-overfit + confirmation live) :
  1. PORTE OOS : la strat est GO au harnais walk-forward purgé + Deflated Sharpe
     (reports/*.md). Un NO-GO n'est JAMAIS éligible, même si son PnL paper paraît bon.
  2. CONFIRMATION LIVE : AvgGross live ≥ coût (~9 bps taker) sur ≥ MIN_LIVE_TRADES.
     AvgGross (brut/notional) est le prédicteur honnête (le net est trop bruité).

Statuts : LIVE_READY (les deux ✓) · PAPER (OOS ok, live insuffisant/sous le coût) ·
REJECT (OOS NO-GO) · UNTESTED (pas de verdict OOS).

Sortie : reports/STRATEGY_ARENA.md + runtime/strategy_arena.json (lu par le GUI).
NE promeut rien automatiquement : il RECOMMANDE ; le passage live reste manuel.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"
FILLS = ROOT / "logs" / "fills_v9.csv"
OUT_JSON = ROOT / "runtime" / "strategy_arena.json"
OUT_MD = REPORTS / "STRATEGY_ARENA.md"

COST_BPS = 9.0              # coût RT taker de référence (seuil AvgGross live)
MIN_LIVE_TRADES = 30        # nb mini de trades live pour confirmer
CAPITAL_PER_STRAT = 500.0   # contrainte utilisateur

# Rapports OOS du harnais → (label, nom live correspondant si existant).
OOS_REPORTS = {
    "HourlyBreakout_ZEC":        ("Breakout 1h ZEC",          "H1Breakout_ZEC"),
    "HourlyBreakout_universe":   ("Breakout 1h univers",       None),
    "AlphaSignalDecile_taker":   ("Décile (taker)",            "AlphaDecile_INJ_LV300"),
    "AlphaSignalDecile_maker":   ("Décile (maker)",            "AlphaDecile_WLD_OBI120"),
    "BTC_5MIN_BINARY":           ("Binaire BTC 5x",            "BTC_5MIN_BINARY_REPL"),
    "TrendFollowingVolTarget":   ("Trend EMA 4h",              None),
    "FundingExtremeReversal":    ("Réversion funding 1h",      None),
    "CrossSectionalReversal":    ("Réversion transversale 1h", None),
    "ResidualBTCReversion":      ("Réversion résidu/BTC 1h",   None),
    "LiquidationCascadeReversal":("Réversion cascade 15m",     None),
}


def parse_oos(stem: str) -> dict | None:
    p = REPORTS / f"{stem}.md"
    if not p.exists():
        return None
    txt = p.read_text(encoding="utf-8")
    go = bool(re.search(r"##\s*[✅🟡].*GO", txt))
    prov = bool(re.search(r"##\s*🟡", txt))
    def grab(pat):
        m = re.search(pat, txt)
        return float(m.group(1)) if m else None
    return {"go": go, "provisional": prov,
            "oos_avgnet_bps": grab(r"AvgNet_bps OOS\*\*\s*:\s*([+-]?\d+\.?\d*)"),
            "dsr": grab(r"DSR=([\d.]+)")}


def live_metrics() -> dict:
    if not FILLS.exists():
        return {}
    df = pd.read_csv(FILLS)
    if df.empty or "strategy" not in df.columns:
        return {}
    for c in ("net", "fee", "gross", "notional"):
        df[c] = pd.to_numeric(df.get(c), errors="coerce")
    df["gross"] = df["gross"].fillna(df["net"].fillna(0) + df["fee"].fillna(0))
    df["notional"] = df["notional"].replace(0, np.nan)
    df["gbps"] = df["gross"] / df["notional"] * 1e4
    out = {}
    for strat, g in df.groupby("strategy"):
        out[str(strat)] = {
            "n": int(len(g)),
            "avg_gross_bps": float(g["gbps"].mean()),
            "net": float(g["net"].sum()),
            "wr": float((g["net"] > 0).mean() * 100),
        }
    return out


def build_arena() -> dict:
    live = live_metrics()
    rows = []
    for stem, (label, live_name) in OOS_REPORTS.items():
        oos = parse_oos(stem)
        lm = live.get(live_name) if live_name else None
        go = bool(oos and oos["go"])
        # éligibilité
        if oos is None:
            status = "UNTESTED"
        elif not go:
            status = "REJECT"           # NO-GO → jamais live
        else:
            if lm and lm["n"] >= MIN_LIVE_TRADES and lm["avg_gross_bps"] >= COST_BPS:
                status = "LIVE_READY"
            else:
                status = "PAPER"        # OOS ok mais live insuffisant/sous coût
        rows.append({
            "strategy": label, "oos_report": stem, "live_name": live_name,
            "oos_go": go, "oos_provisional": bool(oos and oos["provisional"]),
            "oos_avgnet_bps": oos["oos_avgnet_bps"] if oos else None,
            "dsr": oos["dsr"] if oos else None,
            "live_n": lm["n"] if lm else 0,
            "live_avg_gross_bps": round(lm["avg_gross_bps"], 2) if lm else None,
            "live_net": round(lm["net"], 2) if lm else None,
            "live_wr": round(lm["wr"], 1) if lm else None,
            "status": status, "capital_usd": CAPITAL_PER_STRAT,
        })

    order = {"LIVE_READY": 0, "PAPER": 1, "UNTESTED": 2, "REJECT": 3}
    def score(r):
        # rang : statut, puis AvgGross live (sinon AvgNet OOS), décroissant
        live_s = r["live_avg_gross_bps"] if r["live_avg_gross_bps"] is not None else -1e9
        oos_s = r["oos_avgnet_bps"] if r["oos_avgnet_bps"] is not None else -1e9
        return (order[r["status"]], -(live_s if r["live_n"] >= MIN_LIVE_TRADES else oos_s))
    rows.sort(key=score)

    live_ready = [r for r in rows if r["status"] == "LIVE_READY"]
    recommendation = (
        f"PROMOUVOIR EN LIVE : {live_ready[0]['strategy']} "
        f"(AvgGross live {live_ready[0]['live_avg_gross_bps']} bps ≥ {COST_BPS})"
        if live_ready else
        "AUCUNE strat éligible live (aucune n'est à la fois GO en OOS ET AvgGross "
        "live ≥ coût). → tout reste en PAPER. Ne PAS promouvoir sur du PnL paper seul.")
    arena = {
        "generated_at": pd.Timestamp.utcnow().isoformat(),
        "rule": f"LIVE_READY = OOS GO ET AvgGross live ≥ {COST_BPS} bps sur ≥ {MIN_LIVE_TRADES} trades",
        "capital_per_strat_usd": CAPITAL_PER_STRAT,
        "n_live_ready": len(live_ready),
        "recommendation": recommendation,
        "strategies": rows,
    }
    OUT_JSON.parent.mkdir(exist_ok=True)
    OUT_JSON.write_text(json.dumps(arena, indent=2), encoding="utf-8")
    _write_md(arena)
    return arena


def _write_md(a: dict) -> None:
    L = ["# Strategy Arena — comparateur & sélecteur live\n",
         f"*{a['generated_at']} · contrainte {a['capital_per_strat_usd']:.0f} $/strat*\n",
         f"**Règle d'éligibilité live** : {a['rule']}.\n",
         f"## 🎯 Recommandation\n{a['recommendation']}\n",
         "## Classement\n",
         "| Strat | Statut | OOS | OOS AvgNet | DSR | Live n | Live AvgGross | Live net | WR |",
         "|---|---|---|---:|---:|---:|---:|---:|---:|"]
    icon = {"LIVE_READY": "🟢 LIVE", "PAPER": "🟡 PAPER", "UNTESTED": "⏳ UNTESTED", "REJECT": "🔴 REJECT"}
    for r in a["strategies"]:
        oos = ("🟡 GO*" if r["oos_provisional"] else "✅ GO") if r["oos_go"] else (
            "❌ NO-GO" if r["status"] == "REJECT" else "—")
        L.append(f"| {r['strategy']} | {icon[r['status']]} | {oos} | "
                 f"{r['oos_avgnet_bps'] if r['oos_avgnet_bps'] is not None else '—'} | "
                 f"{r['dsr'] if r['dsr'] is not None else '—'} | {r['live_n']} | "
                 f"{r['live_avg_gross_bps'] if r['live_avg_gross_bps'] is not None else '—'} | "
                 f"{r['live_net'] if r['live_net'] is not None else '—'} | "
                 f"{r['live_wr'] if r['live_wr'] is not None else '—'} |")
    L.append("\n*🟡 GO\\* = GO mais non significatif à 95% (à confirmer). Une strat REJECT "
             "(NO-GO OOS) n'est jamais promue, quel que soit son PnL paper — c'est la garde "
             "anti-overfit. AvgGross live est le prédicteur honnête (le net est bruité).*")
    OUT_MD.write_text("\n".join(L), encoding="utf-8")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    a = build_arena()
    print(f"Arena : {len(a['strategies'])} strats · LIVE_READY={a['n_live_ready']}")
    for r in a["strategies"]:
        print(f"  {r['status']:10s} {r['strategy']:26s} OOS_GO={r['oos_go']!s:5s} "
              f"live_n={r['live_n']:4d} live_avg_gross={r['live_avg_gross_bps']}")
    print(f"\n🎯 {a['recommendation']}")
    print(f"\nMD -> {OUT_MD}\nJSON -> {OUT_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
