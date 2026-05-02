"""
app.py — Artemisia v9 monitoring dashboard (Dash + Cyborg theme).

Run from repo root:
    python -m gui.app
Then open http://127.0.0.1:8050

Architecture: read-only CSV reader, completely separate from the engine process.
No import of engine_v9 — zero coupling with asyncio loops.
"""
import dash
import dash_bootstrap_components as dbc
from dash import dcc, html

from gui.theme import COLORS, THEME
from gui.tabs import coins, decisions, overview, risk, trades

app = dash.Dash(
    __name__,
    external_stylesheets=[THEME],
    suppress_callback_exceptions=True,
    title="Artemisia v9",
)

# ── Layout ──────────────────────────────────────────────────────────────────────

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

_TAB_SELECTED_STYLE = {
    "backgroundColor": COLORS["bg"],
    "color":           COLORS["text_light"],
    "borderTop":       f"2px solid {COLORS['accent']}",
    "border":          f"1px solid {COLORS['grid']}",
    "padding":         "6px 16px",
}

app.layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": COLORS["bg"], "minHeight": "100vh",
           "padding": "12px 16px"},
    children=[
        dcc.Interval(id="refresh-interval", interval=5000, n_intervals=0),

        dbc.Row([
            dbc.Col(html.H4("Artemisia v9  —  S8 EMS Monitor", style=_HEADER_STYLE)),
            dbc.Col(
                html.Small("auto-refresh 5s", style={"color": COLORS["text"],
                                                      "float": "right",
                                                      "lineHeight": "2.2"}),
                width="auto",
            ),
        ], style={"marginBottom": "8px"}),

        dbc.Tabs(
            id="main-tabs",
            active_tab="tab-overview",
            children=[
                dbc.Tab(
                    label="Overview",
                    tab_id="tab-overview",
                    tab_style=_TAB_STYLE,
                    active_tab_style=_TAB_SELECTED_STYLE,
                    children=[
                        html.Div(style={"padding": "12px"},
                                 children=overview.static_layout()),
                    ],
                ),
                dbc.Tab(
                    label="Decisions",
                    tab_id="tab-decisions",
                    tab_style=_TAB_STYLE,
                    active_tab_style=_TAB_SELECTED_STYLE,
                    children=[
                        html.Div(style={"padding": "12px"},
                                 children=decisions.static_layout()),
                    ],
                ),
                dbc.Tab(
                    label="Trades",
                    tab_id="tab-trades",
                    tab_style=_TAB_STYLE,
                    active_tab_style=_TAB_SELECTED_STYLE,
                    children=[
                        html.Div(style={"padding": "12px"},
                                 children=trades.static_layout()),
                    ],
                ),
                dbc.Tab(
                    label="Coins",
                    tab_id="tab-coins",
                    tab_style=_TAB_STYLE,
                    active_tab_style=_TAB_SELECTED_STYLE,
                    children=[
                        html.Div(style={"padding": "12px"},
                                 children=coins.static_layout()),
                    ],
                ),
                dbc.Tab(
                    label="Risk",
                    tab_id="tab-risk",
                    tab_style=_TAB_STYLE,
                    active_tab_style=_TAB_SELECTED_STYLE,
                    children=[
                        html.Div(style={"padding": "12px"},
                                 children=risk.static_layout()),
                    ],
                ),
            ],
        ),
    ],
)

# ── Register all tab callbacks ───────────────────────────────────────────────────

overview.register_callbacks(app)
decisions.register_callbacks(app)
trades.register_callbacks(app)
coins.register_callbacks(app)
risk.register_callbacks(app)


# ── Entry point ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app.run(debug=False, host="127.0.0.1", port=8050)
