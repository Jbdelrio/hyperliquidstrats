"""
phase3_retest_b6_d1.py — re-test honnête de B6 (AlphaSignalDecile) et D1 (BTC binary).

Tranche l'hypothèse "il manque juste les bons params" SANS p-hacking : tout passe
par le harnais walk-forward purgé (PHASE 2). Données seconds (~4.6 j) ⇒ confidence
LOW ⇒ verdict provisoire au mieux.

B6 : variante MAKER (capture du spread) vs TAKER — le mur de coût est la vraie
     contrainte. D1 : levier ≤5x APRÈS modélisation de liquidation (jamais
     déclenchée à 5x ⇒ l'edge mesuré est l'edge net directionnel).
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtesting import walkforward as W
from backtesting.seconds_adapters import decile_run_fn, binary_dir_run_fn

ALTS = ["WLD", "INJ", "APE", "BANANA", "KAITO", "COMP", "ARB", "OP"]
GRID = [{"horizon_s": h, "decile": d}
        for h in (60, 120, 300) for d in (0.05, 0.10, 0.20)]
AXES = ["horizon_s", "decile"]


def _show(tag, v):
    sig = "significatif" if v.significant else "NON significatif@95%"
    print(f"  [{tag}] best={v.best_params} AvgNet={v.oos_avg_net_bps:+.2f}bps "
          f"plateau={v.plateau} DSR={v.dsr:.2f}({sig}) "
          f"breadth={v.breadth_pos}/{v.n_coins} stress15={v.stress.get(15.0,0):+.1f} "
          f"→ {'GO' if v.go else 'NO-GO'}")
    if v.reasons:
        print("      rejet:", " ; ".join(v.reasons))


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

    print("=== B6 AlphaSignalDecile — feature=liquidity_vacuum, side=long, alts ===")
    print("--- TAKER ---")
    v_t = W.evaluate_strategy(
        "AlphaSignalDecile_taker", decile_run_fn("liquidity_vacuum", "long", maker=False),
        ALTS, GRID, AXES, interval="1s", confidence="LOW", min_coins=3)
    _show("taker", v_t)
    print("--- MAKER (capture spread, fill optimiste) ---")
    v_m = W.evaluate_strategy(
        "AlphaSignalDecile_maker", decile_run_fn("liquidity_vacuum", "long", maker=True),
        ALTS, GRID, AXES, interval="1s", confidence="LOW", min_coins=3)
    _show("maker", v_m)

    print("\n=== D1 BTC binary directionnel — feature=obi_3, BTC, levier 5x ===")
    v_d = W.evaluate_strategy(
        "BTC_5MIN_BINARY", binary_dir_run_fn("obi_3", "long", leverage=5.0),
        ["BTC"], GRID, AXES, interval="1s", confidence="LOW", min_coins=1)
    _show("D1 5x", v_d)

    print("\n=== VERDICTS PHASE 3 ===")
    for name, v in [("B6 taker", v_t), ("B6 maker", v_m), ("D1 5x", v_d)]:
        print(f"  {name:9s}: {'GO' if v.go else 'NO-GO'}"
              + (" (provisoire LOW)" if v.go else ""))
    print("Rapports -> reports/AlphaSignalDecile_taker.md, _maker.md, reports/BTC_5MIN_BINARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
