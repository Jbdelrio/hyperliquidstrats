"""
gui/tabs/arena.py — onglet "🏆 Arena" : comparateur & sélecteur de stratégies.

Lit runtime/strategy_arena.json (produit par scripts/strategy_arena.py) et affiche
le classement OOS + live, le statut d'éligibilité, et la recommandation de promotion
live. Thème Cyborg. Rafraîchi via refresh-interval.
"""
from __future__ import annotations

import json
from pathlib import Path

from dash import Input, Output, html

from gui.theme import COLORS

_JSON = Path(__file__).resolve().parents[2] / "runtime" / "strategy_arena.json"
M = "JetBrains Mono, Consolas, monospace"
_STATUS_COLOR = {"LIVE_READY": COLORS["success"], "PAPER": COLORS["warning"],
                 "UNTESTED": COLORS["text"], "REJECT": COLORS["danger"]}
_STATUS_LABEL = {"LIVE_READY": "🟢 LIVE-READY", "PAPER": "🟡 PAPER",
                 "UNTESTED": "⏳ UNTESTED", "REJECT": "🔴 REJECT"}


def static_layout() -> list:
    return [
        html.Div([
            html.Span("STRATEGY ARENA", style={"fontWeight": "700", "fontSize": "16px",
                                              "color": COLORS["accent"], "letterSpacing": "1px"}),
            html.Span("comparateur & sélecteur live · 500 $/strat",
                      style={"marginLeft": "12px", "fontSize": "10px", "color": COLORS["text"]}),
        ], style={"marginBottom": "8px"}),
        html.Div(id="arena-reco", style={"marginBottom": "10px", "fontFamily": M,
                                         "fontSize": "13px", "padding": "8px 12px",
                                         "borderRadius": "6px"}),
        html.Div(id="arena-body"),
        html.Div("Éligible LIVE = GO au backtest OOS (anti-overfit) ET AvgGross live ≥ coût "
                 "sur assez de trades. Une strat REJECT (NO-GO) n'est JAMAIS promue, même si "
                 "son PnL paper paraît bon. AvgGross live = le prédicteur honnête.",
                 style={"marginTop": "12px", "fontSize": "10px", "color": COLORS["text"],
                        "fontFamily": M, "opacity": 0.7}),
    ]


def _cell(v, **st):
    base = {"padding": "5px 8px", "fontFamily": M, "fontSize": "11px",
            "borderBottom": f"1px solid {COLORS['grid']}", "textAlign": "left"}
    base.update(st)
    return html.Td(v, style=base)


def _build():
    if not _JSON.exists():
        return (html.Div("Lance :  python scripts/strategy_arena.py",
                         style={"color": COLORS["text"], "fontFamily": M, "padding": "20px"}),
                "", {})
    d = json.loads(_JSON.read_text(encoding="utf-8"))
    cols = ["Stratégie", "Statut", "OOS", "OOS AvgNet", "DSR", "Live n", "Live AvgGross", "Live net", "WR"]
    header = html.Tr([html.Th(c, style={"padding": "5px 8px", "fontFamily": M, "fontSize": "10px",
                                        "color": COLORS["accent"], "textAlign": "left",
                                        "textTransform": "uppercase",
                                        "borderBottom": f"1px solid {COLORS['accent']}55"})
                      for c in cols])
    rows = []
    for r in d.get("strategies", []):
        col = _STATUS_COLOR.get(r["status"], COLORS["text"])
        oos = ("🟡 GO*" if r["oos_provisional"] else "✅ GO") if r["oos_go"] else (
            "❌ NO-GO" if r["status"] == "REJECT" else "—")
        def fmt(x): return "—" if x is None else x
        lag = r["live_avg_gross_bps"]
        lag_col = COLORS["success"] if (lag is not None and lag >= 9) else (
            COLORS["danger"] if lag is not None else COLORS["text"])
        rows.append(html.Tr([
            _cell(r["strategy"], color=COLORS["text_light"], fontWeight="700"),
            _cell(_STATUS_LABEL[r["status"]], color=col, fontWeight="700"),
            _cell(oos),
            _cell(fmt(r["oos_avgnet_bps"])),
            _cell(fmt(r["dsr"])),
            _cell(r["live_n"]),
            _cell(fmt(lag), color=lag_col, fontWeight="700"),
            _cell(fmt(r["live_net"])),
            _cell(fmt(r["live_wr"])),
        ]))
    table = html.Table([html.Thead(header), html.Tbody(rows)],
                       style={"width": "100%", "borderCollapse": "collapse",
                              "backgroundColor": COLORS["card_bg"]})
    return table, d.get("recommendation", ""), d


def register_callbacks(app) -> None:
    @app.callback(
        Output("arena-body", "children"),
        Output("arena-reco", "children"),
        Output("arena-reco", "style"),
        Input("refresh-interval", "n_intervals"),
    )
    def _update(_n):
        table, reco, d = _build()
        ready = d.get("n_live_ready", 0) if d else 0
        col = COLORS["success"] if ready else COLORS["warning"]
        style = {"fontFamily": M, "fontSize": "13px", "padding": "8px 12px",
                 "borderRadius": "6px", "color": col,
                 "border": f"1px solid {col}55", "backgroundColor": "#0c1018"}
        return table, f"🎯 {reco}", style
