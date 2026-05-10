"""
app.py — Artemisia v9 monitoring dashboard (Dash + Cyborg theme).

Run from repo root:
    python -m gui.app           # normal start
    python -m gui.app --fresh   # clear all data files before starting
Then open http://127.0.0.1:8050
"""
import os as _os
import sys as _sys
from pathlib import Path as _Path

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, dcc, html

# ── --fresh : wipe all data files before GUI starts ───────────────────────
if "--fresh" in _sys.argv:
    _REPO = _Path(__file__).parent.parent
    _FRESH_FILES = [
        _REPO / "logs"       / "decisions_v9.csv",
        _REPO / "logs"       / "fills_v9.csv",
        _REPO / "metrics_v9" / "metrics_v9.csv",
        _REPO / "runtime"    / "strategy_status.json",
        _REPO / "runtime"    / "calibration_data.json",
        _REPO / "runtime"    / "control.json",
        _REPO / "runtime"    / "control_result.json",
    ]
    _deleted = [p.name for p in _FRESH_FILES if p.exists() and not p.unlink()]
    print(f"[--fresh] {len(_deleted)} fichier(s) supprimé(s): {', '.join(_deleted) or 'aucun'}")

from gui.control_api import ControlAPI
from gui.theme import COLORS, THEME
from gui.tabs import calibration, coins, decisions, overview, risk, strategies, trades

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_api  = ControlAPI()

app = dash.Dash(
    __name__,
    assets_folder=_os.path.join(_HERE, "assets"),
    external_stylesheets=[THEME],
    suppress_callback_exceptions=True,
    title="Artemisia v9",
)

_HEADER_STYLE = {
    "color":        COLORS["accent"],
    "marginBottom": "0px",
    "fontWeight":   "700",
}
_TAB_STYLE = {
    "backgroundColor": COLORS["card_bg"],
    "color":           COLORS["text"],
    "border":          f"1px solid {COLORS['grid']}",
    "padding":         "6px 16px",
}
_TAB_SEL_STYLE = {
    "backgroundColor": COLORS["bg"],
    "color":           COLORS["text_light"],
    "borderTop":       f"2px solid {COLORS['accent']}",
    "border":          f"1px solid {COLORS['grid']}",
    "padding":         "6px 16px",
}

_TABS = [
    ("Overview",     "tab-overview",     overview),
    ("Decisions",    "tab-decisions",    decisions),
    ("Trades",       "tab-trades",       trades),
    ("Coins",        "tab-coins",        coins),
    ("Risk",         "tab-risk",         risk),
    ("Strategies",   "tab-strategies",   strategies),
    ("Calibration",  "tab-calibration",  calibration),
]

app.layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": COLORS["bg"], "minHeight": "100vh",
           "padding": "12px 16px"},
    children=[
        dcc.Interval(id="refresh-interval", interval=5000, n_intervals=0),

        # ── Header row ────────────────────────────────────────────────
        dbc.Row([
            dbc.Col(html.H4(
                [
                    "Artemisia v9  —  Multi-Strategy Monitor",
                    html.Span("PAPER", className="paper-mode-badge"),
                ],
                style=_HEADER_STYLE,
            )),
            dbc.Col(
                html.Small("auto-refresh 5s",
                           style={"color": COLORS["text"], "float": "right",
                                  "lineHeight": "2.2"}),
                width="auto",
            ),
        ], style={"marginBottom": "6px"}),

        # ── Global connection status bar (always visible) ─────────────
        html.Div(id="global-conn-bar", className=""),

        # ── Tabs ──────────────────────────────────────────────────────
        dbc.Tabs(
            id="main-tabs",
            active_tab="tab-overview",
            children=[
                dbc.Tab(
                    label=label,
                    tab_id=tab_id,
                    tab_style=_TAB_STYLE,
                    active_tab_style=_TAB_SEL_STYLE,
                    children=[html.Div(style={"padding": "12px"},
                                       children=mod.static_layout())],
                )
                for label, tab_id, mod in _TABS
            ],
        ),
    ],
)

# ── Global connection bar callback ─────────────────────────────────────────


@app.callback(
    Output("global-conn-bar", "children"),
    Output("global-conn-bar", "style"),
    Input("refresh-interval", "n_intervals"),
)
def _update_global_conn(_n):
    st = _api.engine_status()
    if st["connected"]:
        dot   = html.Span(className="conn-dot-live")
        msg   = f"Hyperliquid WebSocket  |  heartbeat {st['age_s']}s"
        color = COLORS["success"]
        border = f"1px solid {COLORS['success']}33"
    elif st["running"]:
        dot   = html.Span(className="conn-dot-warn")
        msg   = f"Moteur démarré — en attente heartbeat  |  {st['age_s']}s"
        color = COLORS["warning"]
        border = f"1px solid {COLORS['warning']}33"
    else:
        dot   = html.Span(className="conn-dot-dead")
        msg   = "Moteur non démarré — aller dans Strategies > DÉMARRER"
        color = COLORS["danger"]
        border = f"1px solid {COLORS['danger']}33"

    children = [
        dot,
        html.Span(msg, style={"color": color, "fontSize": "12px",
                               "fontWeight": "600"}),
    ]
    style = {
        "border":          border,
        "borderRadius":    "4px",
        "padding":         "5px 14px",
        "marginBottom":    "8px",
        "backgroundColor": "#0d0d0d",
        "display":         "flex",
        "alignItems":      "center",
        "gap":             "8px",
    }
    return children, style


# ── Register tab callbacks ─────────────────────────────────────────────────

overview.register_callbacks(app)
decisions.register_callbacks(app)
trades.register_callbacks(app)
coins.register_callbacks(app)
risk.register_callbacks(app)
strategies.register_callbacks(app)
calibration.register_callbacks(app)


if __name__ == "__main__":
    import argparse as _ap
    _parser = _ap.ArgumentParser()
    _parser.add_argument("--host", default="127.0.0.1")
    _parser.add_argument("--port", type=int, default=8050)
    _args, _ = _parser.parse_known_args()
    print(f"Dashboard: http://{_args.host}:{_args.port}")
    app.run(debug=False, host=_args.host, port=_args.port)
