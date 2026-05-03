"""
strategies.py — Tab 6: per-strategy status, enable/disable, capital/coin controls.

Reads runtime/strategy_status.json (written by engine every 60s).
Sends commands via ControlAPI (writes runtime/control.json, polls result).
"""
import time

import dash_bootstrap_components as dbc
from dash import Input, Output, State, dcc, html

from gui.control_api import ControlAPI
from gui.data_loader import load_strategy_status
from gui.theme import COLORS, no_data, stat_card

_api = ControlAPI()


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="strategies-table-div"),

        html.Hr(style={"borderColor": COLORS["grid"], "margin": "16px 0"}),
        html.H6("Commande stratégie",
                style={"color": COLORS["text_light"], "marginBottom": "8px"}),

        dbc.Row([
            dbc.Col(
                dcc.Dropdown(
                    id="strat-name-dd",
                    options=[],
                    placeholder="Stratégie...",
                    style={"backgroundColor": COLORS["card_bg"],
                           "color": "#000"},
                ),
                width=3,
            ),
            dbc.Col(
                dcc.Dropdown(
                    id="strat-action-dd",
                    options=[
                        {"label": "Activer",      "value": "enable"},
                        {"label": "Désactiver",   "value": "disable"},
                        {"label": "Reset streak", "value": "reset"},
                        {"label": "Flatten",      "value": "flatten"},
                    ],
                    placeholder="Action...",
                    style={"backgroundColor": COLORS["card_bg"],
                           "color": "#000"},
                ),
                width=3,
            ),
            dbc.Col(
                dbc.Input(
                    id="strat-capital-input",
                    type="number",
                    placeholder="Capital USD (optionnel)",
                    style={"backgroundColor": COLORS["card_bg"],
                           "color": COLORS["text"]},
                ),
                width=3,
            ),
            dbc.Col(
                dbc.Button("Exécuter", id="strat-cmd-btn",
                           color="primary", size="sm"),
                width="auto",
            ),
        ], className="g-2"),

        html.Div(id="strat-cmd-result",
                 style={"color": COLORS["success"], "marginTop": "8px",
                        "fontSize": "12px", "fontFamily": "monospace"}),

        html.Hr(style={"borderColor": COLORS["grid"], "margin": "16px 0"}),
        html.H6("Contrôles globaux",
                style={"color": COLORS["text_light"], "marginBottom": "8px"}),

        dbc.Row([
            dbc.Col(dbc.Button("FLATTEN ALL", id="g-btn-flatten-all",
                               color="danger",    size="sm"), width="auto"),
            dbc.Col(dbc.Button("PAUSE 60m",   id="g-btn-pause-all",
                               color="warning",   size="sm"), width="auto"),
            dbc.Col(dbc.Button("TRADING ON",  id="g-btn-trading-on",
                               color="success",   size="sm"), width="auto"),
            dbc.Col(dbc.Button("TRADING OFF", id="g-btn-trading-off",
                               color="secondary", size="sm"), width="auto"),
        ], className="g-2"),

        html.Div(id="global-cmd-result",
                 style={"color": COLORS["success"], "marginTop": "8px",
                        "fontSize": "12px", "fontFamily": "monospace"}),
    ])


def register_callbacks(app) -> None:

    # ── Strategy status table ────────────────────────────────────────────

    @app.callback(
        Output("strategies-table-div", "children"),
        Output("strat-name-dd", "options"),
        Input("refresh-interval", "n_intervals"),
    )
    def update_table(n):
        status = load_strategy_status()
        if not status:
            return no_data("En attente de runtime/strategy_status.json..."), []

        rows = []
        for s in status:
            name      = s.get("name", "?")
            enabled   = s.get("enabled", False)
            capital   = s.get("capital_allocated_usd", 0)
            coins     = s.get("coins", [])
            losses    = s.get("consecutive_losses", 0)
            susp_ts   = float(s.get("suspended_until", 0))
            params    = s.get("params", {})

            susp_badge = ""
            if susp_ts > time.time():
                rem = int(susp_ts - time.time())
                susp_badge = dbc.Badge(f"Suspendu {rem}s",
                                       color="warning", className="ms-2")

            rows.append(
                dbc.Card([
                    dbc.CardHeader(
                        dbc.Row([
                            dbc.Col(html.B(name,
                                           style={"color": COLORS["accent"]})),
                            dbc.Col(html.Span([
                                dbc.Badge(
                                    "ACTIF" if enabled else "INACTIF",
                                    color="success" if enabled else "danger",
                                ),
                                susp_badge,
                            ]), width="auto"),
                        ], align="center"),
                        style={"backgroundColor": COLORS["card_bg"],
                               "padding": "6px 12px"},
                    ),
                    dbc.CardBody(
                        dbc.Row([
                            dbc.Col(stat_card("Capital",
                                              f"${capital:.0f}",
                                              COLORS["accent"]), width=2),
                            dbc.Col(stat_card("Coins",
                                              str(len(coins)),
                                              COLORS["text"]), width=2),
                            dbc.Col(stat_card("Streak pertes",
                                              str(losses),
                                              COLORS["danger"] if losses >= 3
                                              else COLORS["text"]), width=2),
                            dbc.Col(
                                html.Details([
                                    html.Summary("params",
                                                 style={"color": COLORS["text"],
                                                        "cursor": "pointer",
                                                        "fontSize": "11px"}),
                                    html.Pre(
                                        "\n".join(f"{k}: {v}"
                                                  for k, v in list(params.items())[:8]),
                                        style={"fontSize": "10px",
                                               "color": COLORS["text"],
                                               "margin": "4px 0 0 8px"},
                                    ),
                                ]),
                                width=6,
                            ),
                        ], className="g-2"),
                        style={"backgroundColor": COLORS["card_bg"],
                               "padding": "8px 12px"},
                    ),
                ],
                style={"marginBottom": "8px",
                       "border": f"1px solid {COLORS['grid']}"},
                )
            )

        options = [{"label": s.get("name", "?"), "value": s.get("name", "?")}
                   for s in status]
        return html.Div(rows), options

    # ── Strategy command ─────────────────────────────────────────────────

    @app.callback(
        Output("strat-cmd-result", "children"),
        Input("strat-cmd-btn", "n_clicks"),
        State("strat-name-dd", "value"),
        State("strat-action-dd", "value"),
        State("strat-capital-input", "value"),
        prevent_initial_call=True,
    )
    def execute_strat_cmd(n_clicks, name, action, capital):
        if not name or not action:
            return "⚠ Sélectionner stratégie et action."
        try:
            if action == "flatten":
                result = _api.flatten_strategy(name)
            elif action == "enable":
                result = _api.enable_strategy(name)
            elif action == "disable":
                result = _api.disable_strategy(name)
            elif action == "reset":
                result = _api.reset_strategy(name)
            else:
                return f"⚠ Action inconnue: {action}"

            if capital and action != "flatten":
                _api.set_capital(name, float(capital))

            if result.get("ok"):
                return f"✓ {action} {name} — OK"
            return f"✗ {result.get('error', 'erreur inconnue')}"
        except Exception as e:
            return f"✗ Exception: {e}"

    # ── Global commands ──────────────────────────────────────────────────

    @app.callback(
        Output("global-cmd-result", "children"),
        Input("g-btn-flatten-all",  "n_clicks"),
        Input("g-btn-pause-all",    "n_clicks"),
        Input("g-btn-trading-on",   "n_clicks"),
        Input("g-btn-trading-off",  "n_clicks"),
        prevent_initial_call=True,
    )
    def global_cmd(n_flatten, n_pause, n_on, n_off):
        from dash import ctx
        trig = ctx.triggered_id
        try:
            if trig == "g-btn-flatten-all":
                r = _api.flatten_all()
                return f"✓ FLATTEN ALL — pnl={r.get('total_pnl', '?')}"
            elif trig == "g-btn-pause-all":
                r = _api.pause_all(60)
                return f"✓ PAUSE 60min — jusqu'à {time.strftime('%H:%M:%S', time.localtime(r.get('pause_until', 0)))}"
            elif trig == "g-btn-trading-on":
                r = _api.set_trading(True)
                return "✓ TRADING ON"
            elif trig == "g-btn-trading-off":
                r = _api.set_trading(False)
                return "✓ TRADING OFF"
        except Exception as e:
            return f"✗ {e}"
        return ""
