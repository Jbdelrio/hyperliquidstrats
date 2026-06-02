"""
gui/tabs/metarbitrage.py — onglet de monitoring live du spread cross-venues.

Lecture seule : affiche runtime/metarbitrage.json produit par
`python -m metarbitrage.scanner`. Thème Cyborg (COLORS). Rafraîchi via
l'intervalle global (refresh-interval). AUCUNE exécution.
"""
from __future__ import annotations

import json
from pathlib import Path

from dash import Input, Output, html

from gui.theme import COLORS

_RUNTIME_JSON = Path(__file__).resolve().parents[2] / "runtime" / "metarbitrage.json"
M = "JetBrains Mono, Consolas, monospace"


def static_layout() -> list:
    return [
        html.Div([
            html.Span("METARBITRAGE", style={"fontWeight": "700", "fontSize": "16px",
                                             "color": COLORS["accent"], "letterSpacing": "1px"}),
            html.Span("MONITEUR — MESURE UNIQUEMENT · aucun trade · aucune clé",
                      style={"marginLeft": "12px", "fontSize": "10px", "color": COLORS["warning"],
                             "border": f"1px solid {COLORS['warning']}55", "borderRadius": "3px",
                             "padding": "2px 8px", "letterSpacing": "1px"}),
        ], style={"marginBottom": "10px"}),
        html.Div(id="metarb-header", style={"marginBottom": "10px", "fontFamily": M,
                                            "fontSize": "12px", "color": COLORS["text"]}),
        html.Div(id="metarbitrage-body"),
        html.Div("Spread NET = brut − (frais achat + frais vente) − slippage. Vert = net positif "
                 "(rare, à vérifier) ; rouge = net négatif (pas d'arb) ; ⚠️ SUSPECT = écart "
                 "anormal (token illiquide / quote périmée) = LEURRE, intradeable.",
                 style={"marginTop": "12px", "fontSize": "10px", "color": COLORS["text"],
                        "fontFamily": M, "opacity": 0.7}),
    ]


def _cell(v, **st):
    base = {"padding": "5px 8px", "fontFamily": M, "fontSize": "11px",
            "borderBottom": f"1px solid {COLORS['grid']}", "textAlign": "left"}
    base.update(st)
    return html.Td(v, style=base)


def _build_body():
    if not _RUNTIME_JSON.exists():
        return html.Div("En attente du moniteur. Lance :  python -m metarbitrage.scanner",
                        style={"color": COLORS["text"], "fontFamily": M, "padding": "20px"})
    try:
        d = json.loads(_RUNTIME_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        return html.Div(f"lecture metarbitrage.json échouée : {e}",
                        style={"color": COLORS["danger"], "fontFamily": M})

    hdr = (f"maj {d.get('ts','?')} · venues {', '.join(d.get('live_venues', []))} · "
           f"univers {d.get('universe_size','?')} coins ≥500M · "
           f"net positif (hors leurre) : {d.get('n_positive_net',0)} · "
           f"meilleur net {d.get('best_net_bps','?')} bps")

    cols = ["Coin", "MktCap M$", "Acheter @", "Vendre @", "Brut bps", "Coût bps", "NET bps", ""]
    header = html.Tr([html.Th(c, style={"padding": "5px 8px", "fontFamily": M, "fontSize": "10px",
                                        "color": COLORS["accent"], "textAlign": "left",
                                        "textTransform": "uppercase",
                                        "borderBottom": f"1px solid {COLORS['accent']}55"})
                      for c in cols])
    rows = []
    for o in d.get("opportunities", []):
        if o.get("suspect"):
            net_col, flag = COLORS["warning"], "⚠️ SUSPECT (leurre)"
        elif o["net_bps"] > 0:
            net_col, flag = COLORS["success"], "net + (vérifier)"
        else:
            net_col, flag = COLORS["danger"], ""
        rows.append(html.Tr([
            _cell(o["coin"], color=COLORS["text_light"], fontWeight="700"),
            _cell(f"{o['mktcap_m']:,}"),
            _cell(f"{o['buy_venue']} @ {o['buy_ask']:.6g}"),
            _cell(f"{o['sell_venue']} @ {o['sell_bid']:.6g}"),
            _cell(f"{o['gross_bps']:+.1f}"),
            _cell(f"{o['cost_bps']:.0f}", color=COLORS["text"]),
            _cell(f"{o['net_bps']:+.1f}", color=net_col, fontWeight="700"),
            _cell(flag, color=net_col, fontSize="10px"),
        ]))
    table = html.Table([html.Thead(header), html.Tbody(rows)],
                       style={"width": "100%", "borderCollapse": "collapse",
                              "backgroundColor": COLORS["card_bg"]})
    return html.Div([html.Div(hdr, id="_metarb_hdr_inline", style={"display": "none"}), table])


def register_callbacks(app) -> None:
    @app.callback(
        Output("metarbitrage-body", "children"),
        Output("metarb-header", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def _update(_n):
        body = _build_body()
        hdr = ""
        if _RUNTIME_JSON.exists():
            try:
                d = json.loads(_RUNTIME_JSON.read_text(encoding="utf-8"))
                hdr = (f"maj {d.get('ts','?')} · venues {', '.join(d.get('live_venues', []))} · "
                       f"univers {d.get('universe_size','?')} ≥500M · net+ (hors leurre) : "
                       f"{d.get('n_positive_net',0)}")
            except Exception:
                hdr = ""
        return body, hdr
