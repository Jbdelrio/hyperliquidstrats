"""
recalibrate_preset.py — recalibration "saine" d'un preset (anti-mort-par-frais).

Constat (logs réels) : les scalpers (OBImbalanceScalper, MeanReversionKalman…) se
font bouffer par des STOPS TROP SERRÉS + les frais (Kalman sortait en 0.8s). On
recalibre, par règles transparentes :

  1. STOPS : on RETIRE les stops intrabar trop serrés (stop_loss_* en dessous d'un
     seuil sain) → on s'appuie sur le time-stop + la liquidation (loin à ≤5x). On
     arrête de se faire couper par le bruit.
  2. HOLDS : on ALLONGE les holds trop courts (< min_hold) pour laisser le mouvement
     se développer.
  3. LEVIER : plafonné à 5x (>5x = asymétrie de liquidation = ruine). On monte les
     petits leviers à 5x, on REDESCEND les 25x/150x à 5x.
  4. SIZING : notional = margin × levier visé ~500 $ (contrainte 500 $/strat).

Sortie : un nouveau preset config/presets/<nom>_recal.json (l'original intact).
NB : recalibrer ≠ créer de l'alpha. Ça rend le test JUSTE et SÛR ; le verdict
(Arena / OOS) reste ce que dit la data.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRESETS = ROOT / "config" / "presets"

MAX_LEV = 5.0
MIN_HOLD_S = 1800          # 30 min : plancher de détention pour les scalpers
TIGHT_STOP_PCT = 0.01      # < 1% = trop serré → retiré
TIGHT_STOP_BPS = 60.0      # < 60 bps = trop serré → retiré
TARGET_NOTIONAL = 500.0


def _cap_leverage(p: dict) -> list:
    notes = []
    for k in ("leverage", "max_leverage"):
        if k in p and isinstance(p[k], (int, float)):
            if p[k] > MAX_LEV:
                notes.append(f"{k} {p[k]}→{MAX_LEV:.0f} (plafond anti-ruine)")
                p[k] = MAX_LEV
            elif k == "leverage" and p[k] < MAX_LEV:
                notes.append(f"leverage {p[k]}→{MAX_LEV:.0f} (au plafond sûr)")
                p[k] = MAX_LEV
    # sizing : viser ~500 $ de notional via la marge
    if "leverage" in p:
        if "margin_usd" in p:
            p["margin_usd"] = round(TARGET_NOTIONAL / p["leverage"], 2)
        if "max_margin_per_trade_usd" in p:
            p["max_margin_per_trade_usd"] = round(TARGET_NOTIONAL / p["leverage"], 2)
    return notes


def _loosen_stops(p: dict) -> list:
    """Élargit les stops trop serrés (sans les mettre à None — éviterait un crash
    arithmétique dans les stratégies legacy). Stop large ≈ jamais touché par le
    bruit ; on s'appuie de fait sur le time-stop + la liquidation (loin à ≤5x)."""
    notes = []
    WIDE_PCT, WIDE_BPS, WIDE_USD = 0.05, 200.0, 50.0
    for k in ("stop_loss_pct",):
        v = p.get(k)
        if isinstance(v, (int, float)) and 0 < v < TIGHT_STOP_PCT:
            p[k] = WIDE_PCT
            notes.append(f"{k} {v}→{WIDE_PCT} (stop élargi)")
    for k in ("stop_loss_bps",):
        v = p.get(k)
        if isinstance(v, (int, float)) and 0 < v < TIGHT_STOP_BPS:
            p[k] = WIDE_BPS
            notes.append(f"{k} {v}→{WIDE_BPS:.0f} (stop élargi)")
    for k in ("stop_loss_usd",):
        v = p.get(k)
        if isinstance(v, (int, float)) and 0 < v < WIDE_USD:
            p[k] = WIDE_USD
            notes.append(f"{k} {v}→{WIDE_USD:.0f} (stop élargi)")
    # MeanReversionKalman : élargir le z_stop (touché par le bruit). On NE touche
    # PAS z_exit (le baisser raccourcirait la détention).
    v = p.get("z_stop")
    if isinstance(v, (int, float)) and v < 6.0:
        p["z_stop"] = 6.0
        notes.append(f"z_stop {v}→6.0 (desserré, anti-bruit)")
    return notes


def _extend_holds(p: dict) -> list:
    notes = []
    v = p.get("max_hold_seconds")
    if isinstance(v, (int, float)) and v < MIN_HOLD_S:
        p["max_hold_seconds"] = MIN_HOLD_S
        notes.append(f"max_hold_seconds {v}→{MIN_HOLD_S}")
    v = p.get("max_holding_seconds")
    if isinstance(v, (int, float)) and v < MIN_HOLD_S:
        p["max_holding_seconds"] = MIN_HOLD_S
        notes.append(f"max_holding_seconds {v}→{MIN_HOLD_S}")
    v = p.get("max_hold_minutes")
    if isinstance(v, (int, float)) and v < MIN_HOLD_S / 60:
        p["max_hold_minutes"] = MIN_HOLD_S // 60
        notes.append(f"max_hold_minutes {v}→{MIN_HOLD_S//60}")
    return notes


def recalibrate(preset_name: str, enable: list, out_name: str | None = None) -> str:
    src = PRESETS / preset_name
    d = json.loads(src.read_text(encoding="utf-8"))
    enable = set(enable or [])
    print(f"Recalibration de {preset_name} (levier≤{MAX_LEV:.0f}x, stops serrés retirés, "
          f"hold≥{MIN_HOLD_S//60}min)\n")
    for s in d.get("strategies", []):
        p = s.get("params") or {}
        notes = _cap_leverage(p) + _loosen_stops(p) + _extend_holds(p)
        s["params"] = p
        if s["name"] in enable:
            s["enabled"] = True
        if notes:
            print(f"  {s['name']:24s} {'[ON]' if s.get('enabled') else '[off]'}: "
                  + " ; ".join(notes))
    out = out_name or preset_name.replace(".json", "_recal.json")
    (PRESETS / out).write_text(json.dumps(d, indent=2), encoding="utf-8")
    print(f"\n→ {PRESETS / out}")
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--preset", default="paper_500_all_active.json")
    ap.add_argument("--enable", default="",
                    help="strats à (ré)activer, séparées par des virgules")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    enable = [x.strip() for x in args.enable.split(",") if x.strip()]
    recalibrate(args.preset, enable, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
