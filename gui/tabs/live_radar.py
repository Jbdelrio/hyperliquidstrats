"""
gui/tabs/live_radar.py — onglet "📡 Live" : radar de monitoring intelligent.

Sur UN écran, pour chaque stratégie/coin chargé par le moteur :
  - PROXIMITÉ D'ENTRÉE : distance (bps) avant la cassure la plus proche (barre +
    countdown) → « à quel point on est près de prendre une position ».
  - GATE de volatilité (ATR) : ouvert/fermé.
  - POSITION : en position ou non.
  - EFFICIENCE : AvgGross live vs coût (depuis l'Arena) → « la strat est-elle
    efficiente ou pas ».

Lit runtime/calibration_data.json (proximité, écrit par le moteur) +
runtime/strategy_arena.json (efficience). Lecture seule, refresh global.
"""
from __future__ import annotations

import json
from pathlib import Path

from dash import Input, Output, html

from gui.theme import COLORS

_RT = Path(__file__).resolve().parents[2] / "runtime"
_CALIB = _RT / "calibration_data.json"
_ARENA = _RT / "strategy_arena.json"
M = "JetBrains Mono, Consolas, monospace"
_PROX_MAX_BPS = 300.0     # échelle de la barre de proximité (0 = sur le seuil)


def static_layout() -> list:
    return [
        html.Div([
            html.Span("📡 RADAR LIVE", style={"fontWeight": "700", "fontSize": "16px",
                                            "color": COLORS["accent"], "letterSpacing": "1px"}),
            html.Span("proximité d'entrée + efficience, par stratégie/coin",
                      style={"marginLeft": "12px", "fontSize": "10px", "color": COLORS["text"]}),
        ], style={"marginBottom": "10px"}),
        html.Div(id="radar-body"),
        html.Div("Proximité = distance (bps) avant la cassure la plus proche (barre pleine = "
                 "imminent). Gate ATR = assez de volatilité pour trader. Efficience = AvgGross "
                 "live vs coût (vert = couvre le coût). Lecture seule ; le moteur doit tourner.",
                 style={"marginTop": "12px", "fontSize": "10px", "color": COLORS["text"],
                        "fontFamily": M, "opacity": 0.7}),
    ]


def _bar(frac: float, color: str, w: int = 140) -> html.Div:
    frac = max(0.0, min(1.0, frac))
    return html.Div(style={"width": f"{w}px", "height": "9px", "backgroundColor": "#0c1018",
                           "border": f"1px solid {COLORS['grid']}", "borderRadius": "999px",
                           "display": "inline-block", "verticalAlign": "middle"},
                    children=[html.Div(style={"width": f"{frac*100:.0f}%", "height": "100%",
                                              "backgroundColor": color, "borderRadius": "999px"})])


def _arena_eff() -> dict:
    """{live_name: avg_gross_bps} depuis l'Arena (efficience live)."""
    if not _ARENA.exists():
        return {}
    try:
        a = json.loads(_ARENA.read_text(encoding="utf-8"))
        return {r.get("live_name") or r.get("strategy"): r.get("live_avg_gross_bps")
                for r in a.get("strategies", [])}
    except Exception:
        return {}


def _build():
    if not _CALIB.exists():
        return html.Div("En attente de runtime/calibration_data.json (moteur lancé ?)",
                        style={"color": COLORS["text"], "fontFamily": M, "padding": "20px"})
    try:
        cal = json.loads(_CALIB.read_text(encoding="utf-8"))
    except Exception as e:
        return html.Div(f"lecture calib échouée : {e}", style={"color": COLORS["danger"]})
    eff = _arena_eff()

    cols = ["Stratégie / coin", "Proximité d'entrée (bps avant cassure)", "Gate vol",
            "Position", "Efficience (AvgGross live)"]
    header = html.Tr([html.Th(c, style={"padding": "6px 10px", "fontFamily": M, "fontSize": "10px",
                                        "color": COLORS["accent"], "textAlign": "left",
                                        "textTransform": "uppercase",
                                        "borderBottom": f"1px solid {COLORS['accent']}55"})
                      for c in cols])
    rows = []
    for strat, coins in cal.items():
        if not isinstance(coins, dict):
            continue
        for coin, d in coins.items():
            if not isinstance(d, dict):
                continue
            td = lambda v, **st: html.Td(v, style={"padding": "6px 10px", "fontFamily": M,
                                                    "fontSize": "11px",
                                                    "borderBottom": f"1px solid {COLORS['grid']}", **st})
            # proximité (breakout) : distance à la cassure la plus proche
            dl, ds = d.get("dist_to_long_bps"), d.get("dist_to_short_bps")
            if dl is not None and ds is not None:
                near = min(abs(dl), abs(ds))
                frac = 1.0 - min(near, _PROX_MAX_BPS) / _PROX_MAX_BPS
                col = (COLORS["danger"] if near <= 20 else
                       COLORS["warning"] if near <= 100 else COLORS["success"])
                prox = html.Span([_bar(frac, col),
                                  html.Span(f"  {near:.0f} bps", style={"color": col, "marginLeft": "6px"})])
            elif not d.get("ready", True):
                prox = html.Span("warmup…", style={"color": COLORS["text"], "opacity": 0.6})
            else:
                prox = html.Span("n/a (pas un breakout)", style={"color": COLORS["text"], "opacity": 0.5})
            gate = ("✅" if d.get("atr_gate_open") else
                    ("⚠️" if "atr_gate_open" in d else "—"))
            pos = "🟢 OUI" if d.get("in_position") else "—"
            ag = eff.get(strat)
            if ag is None:
                effc = html.Span("—", style={"opacity": 0.5})
            else:
                ec = COLORS["success"] if ag >= 9 else COLORS["danger"]
                effc = html.Span(f"{ag:+.1f} bps", style={"color": ec, "fontWeight": "700"})
            rows.append(html.Tr([
                td(f"{strat} · {coin}", color=COLORS["text_light"], fontWeight="700"),
                td(prox), td(gate, fontSize="13px"), td(pos), td(effc),
            ]))
    if not rows:
        return html.Div("Aucune donnée de calibration (warmup ou moteur non lancé).",
                        style={"color": COLORS["text"], "fontFamily": M, "padding": "20px"})
    return html.Table([html.Thead(header), html.Tbody(rows)],
                      style={"width": "100%", "borderCollapse": "collapse",
                             "backgroundColor": COLORS["card_bg"]})


def register_callbacks(app) -> None:
    @app.callback(Output("radar-body", "children"),
                  Input("refresh-interval", "n_intervals"))
    def _update(_n):
        return _build()
