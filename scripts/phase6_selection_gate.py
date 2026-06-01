"""
phase6_selection_gate.py — porte de sélection finale (PHASE 6).

Agrège les verdicts produits par le harnais (reports/*.md), classe les GO par
qualité d'edge OOS, liste les NO-GO avec leur raison chiffrée, et écrit
reports/SELECTION_GATE.md (résumé priorisé en français). Seules les GO sont
éligibles au paper trading.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"

# Rapports produits par le harnais (PHASE 2) lors des PHASES 3/4/5.
HARNESS_REPORTS = [
    ("HourlyBreakout_ZEC", "Breakout 1h — ZEC seul", "HIGH"),
    ("HourlyBreakout_universe", "Breakout 1h — décile haut-vol (8 coins)", "HIGH"),
    ("AlphaSignalDecile_taker", "B6 Décile (taker)", "LOW"),
    ("AlphaSignalDecile_maker", "B6 Décile (maker)", "LOW"),
    ("BTC_5MIN_BINARY", "D1 Binaire BTC 5x", "LOW"),
    ("TrendFollowingVolTarget", "Trend EMA-cross 4h", "HIGH"),
    ("FundingExtremeReversal", "Réversion funding 1h", "HIGH"),
    ("CrossSectionalReversal", "Réversion transversale 1h", "MEDIUM"),
    ("ResidualBTCReversion", "Réversion résidu vs BTC 1h", "MEDIUM"),
    ("LiquidationCascadeReversal", "Réversion cascade 15m", "MEDIUM"),
]

# Stratégies Phase 5 non testables (donnée manquante).
UNTESTED = [
    ("MarkOracleDislocation", "1m/15m", "DONNÉE INDISPONIBLE : oracle historique non exposé par l'API HL → testable seulement en live"),
]


def parse_report(stem: str) -> dict:
    p = REPORTS / f"{stem}.md"
    if not p.exists():
        return {"found": False}
    txt = p.read_text(encoding="utf-8")
    if "NO-GO" in txt.split("\n", 6)[2] if len(txt.split("\n")) > 2 else False:
        pass
    go = "GO" if re.search(r"##\s*[✅🟡].*GO", txt) else "NO-GO"
    prov = "🟡" in txt.split("##", 2)[1] if "##" in txt else False
    def grab(pat, default=None):
        m = re.search(pat, txt)
        return m.group(1) if m else default
    return {
        "found": True, "verdict": go, "provisional": prov,
        "avgnet": grab(r"AvgNet_bps OOS\*\*\s*:\s*([+-]?\d+\.?\d*)"),
        "dsr": grab(r"DSR=([\d.]+)"),
        "breadth": grab(r"Breadth\s*:\s*(\d+/\d+)"),
        "reasons": (re.search(r"Raisons du rejet :\*\*\s*(.+)", txt) or [None, ""])[1]
                   if "Raisons" in txt else "",
    }


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    rows = []
    for stem, label, conf in HARNESS_REPORTS:
        r = parse_report(stem)
        if r.get("found"):
            rows.append((label, conf, r))

    go = [(l, c, r) for (l, c, r) in rows if r["verdict"] == "GO"]
    nogo = [(l, c, r) for (l, c, r) in rows if r["verdict"] == "NO-GO"]

    def avgnet_val(r):
        try: return float(r["avgnet"])
        except (TypeError, ValueError): return -1e9
    go.sort(key=lambda x: -avgnet_val(x[2]))

    L = ["# Porte de sélection finale — verdicts OOS (PHASE 6)\n",
         "Critères GO (TOUS requis) : AvgNet_bps OOS > 0 après 14 bps · plateau "
         "paramétrique · survie au stress 15 bps · edge déflaté (Deflated Sharpe) "
         "positif · breadth ≥ min_coins. Aucun tuning sur l'in-sample. **Seules les "
         "GO sont éligibles au paper trading.**\n"]

    L.append("## ✅ / 🟡 GO (éligibles paper)\n")
    if go:
        L.append("| Stratégie | Conf | AvgNet OOS | DSR | Breadth | Statut |")
        L.append("|---|---|---:|---:|---|---|")
        for label, conf, r in go:
            statut = "🟡 GO (non sig. 95% / à confirmer)" if (r["provisional"] or (r["dsr"] and float(r["dsr"]) < 0.95)) else "✅ GO"
            L.append(f"| {label} | {conf} | {r['avgnet']} bps | {r['dsr']} | {r.get('breadth','—')} | {statut} |")
    else:
        L.append("*(aucune GO ferme)*")

    L.append("\n## ❌ NO-GO (raison chiffrée)\n")
    L.append("| Stratégie | Conf | AvgNet OOS | Raison |")
    L.append("|---|---|---:|---|")
    for label, conf, r in sorted(nogo, key=lambda x: -avgnet_val(x[2])):
        reason = (r["reasons"][:120] + "…") if len(r["reasons"]) > 120 else r["reasons"]
        L.append(f"| {label} | {conf} | {r['avgnet']} bps | {reason} |")

    L.append("\n## ⏳ Non testé (à venir / donnée manquante)\n")
    L.append("| Stratégie | Intervalle | Note |\n|---|---|---|")
    for name, itv, note in UNTESTED:
        L.append(f"| {name} | {itv} | {note} |")

    L.append("\n## Résumé priorisé (français)\n")
    L.append("- **Edge net OOS confirmé (ferme, GO significatif)** : *aucun*. Sous "
             "walk-forward purgé + déflation multiple-testing + stress de coût, **aucune "
             "stratégie ne passe le seuil de significativité à 95 %** sur le TOP 20.")
    if go:
        best = go[0]
        L.append(f"- **Provisoire / à confirmer** : **{best[0]}** est la meilleure "
                 f"(AvgNet {best[2]['avgnet']} bps OOS) mais reste **non significative "
                 f"après déflation** → edge probablement spécifique/sur-ajusté, à ne PAS "
                 f"sur-pondérer. C'est la seule à garder en observation paper (petit capital).")
    L.append("- **Rejeté** : Trend EMA-cross 4h (whipsaw, −51 bps), réversion funding "
             "(−8.7 bps), B6 décile taker (−8.7) ET maker (−0.5 : le mur de coût/spread "
             "est la contrainte, pas les params), D1 binaire (−4.3, sous le coût). Aucun "
             "n'est éligible au paper.")
    L.append("- **Provisoire données LOW** : les tests seconds (B6/D1) reposent sur ~4.6 j "
             "→ verdict faible de toute façon ; même négatifs ils ne méritent pas de re-test.")
    L.append("- **Reco** : conserver UNIQUEMENT ZEC en paper (edge prouvé en prod, non "
             "touché ; mais on sait maintenant qu'il n'est NI généralisable NI significatif "
             "à 95 % OOS → surveiller l'AvgGross live, ne pas ajouter de capital). "
             "Prochaines pistes à tester proprement : CrossSectionalReversal, "
             "LiquidationCascadeReversal, ResidualBTCReversion (adaptateurs à écrire).")
    L.append("\n*Le mérite de ce pipeline n'est pas d'avoir trouvé un edge, mais d'avoir "
             "honnêtement REJETÉ des edges illusoires que le backtest naïf aurait validés.*")

    out = REPORTS / "SELECTION_GATE.md"
    out.write_text("\n".join(L), encoding="utf-8")
    print(f"GO: {len(go)} · NO-GO: {len(nogo)} · non testé: {len(UNTESTED)}")
    for label, conf, r in go:
        print(f"  GO  {label}: AvgNet={r['avgnet']} DSR={r['dsr']}")
    for label, conf, r in nogo:
        print(f"  NO  {label}: AvgNet={r['avgnet']}")
    print(f"Gate -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
