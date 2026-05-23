"""Top control row — coin / exchange selectors + refresh / alert."""
from __future__ import annotations

from dash import dcc, html
import dash_bootstrap_components as dbc

from config.endpoints import EXCHANGES
from config.settings import DARK_THEME, DEFAULT_COINS, DEFAULT_EXCHANGES, GUI_REFRESH_MS

COIN_OPTIONS = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "AVAX", "LINK", "MATIC"]
EXCHANGE_OPTIONS = list(EXCHANGES.keys())


def build_controls() -> dbc.Card:
    dropdown_style = {
        "backgroundColor": DARK_THEME["card"],
        "color": "#000",
        "border": f"1px solid {DARK_THEME['border']}",
    }
    return dbc.Card(
        dbc.CardBody(
            dbc.Row([
                dbc.Col([
                    html.Label("Coins", style={"color": DARK_THEME["text_dim"]}),
                    dcc.Dropdown(
                        id="coin-selector",
                        options=[{"label": c, "value": c} for c in COIN_OPTIONS],
                        value=DEFAULT_COINS,
                        multi=True,
                        style=dropdown_style,
                        clearable=False,
                    ),
                ], md=4),
                dbc.Col([
                    html.Label("Exchanges", style={"color": DARK_THEME["text_dim"]}),
                    dcc.Dropdown(
                        id="exchange-selector",
                        options=[{"label": e.title(), "value": e} for e in EXCHANGE_OPTIONS],
                        value=DEFAULT_EXCHANGES,
                        multi=True,
                        style=dropdown_style,
                        clearable=False,
                    ),
                ], md=4),
                dbc.Col([
                    html.Label("Alert (bps)", style={"color": DARK_THEME["text_dim"]}),
                    dcc.Input(
                        id="alert-threshold",
                        type="number",
                        value=5.0,
                        min=0,
                        step=0.1,
                        debounce=True,
                        style={
                            "width": "100%",
                            "backgroundColor": DARK_THEME["background"],
                            "color": DARK_THEME["text"],
                            "border": f"1px solid {DARK_THEME['border']}",
                            "padding": "6px",
                            "borderRadius": "6px",
                        },
                    ),
                ], md=2),
                dbc.Col([
                    html.Label(" ", style={"color": DARK_THEME["text_dim"]}),
                    dbc.Button(
                        "↻ Refresh",
                        id="refresh-btn",
                        color="primary",
                        className="w-100",
                        n_clicks=0,
                    ),
                ], md=2),
            ], className="g-3"),
            style={"backgroundColor": DARK_THEME["card"]},
        ),
        style={"backgroundColor": DARK_THEME["card"], "border": f"1px solid {DARK_THEME['border']}"},
    ), dcc.Interval(id="auto-refresh", interval=GUI_REFRESH_MS, n_intervals=0)
