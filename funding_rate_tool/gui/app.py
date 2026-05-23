"""Dash app — dark-mode multi-tab funding-rate dashboard."""
from __future__ import annotations

import json
from typing import Dict, List

import dash
from dash import Input, Output, State, dcc, html
import dash_bootstrap_components as dbc

from cli.fetcher import fetch_many_sync, fetch_top_n_sync
from config.endpoints import BULK_EXCHANGES
from config.settings import DARK_THEME, GUI_HOST, GUI_PORT
from gui.components.charts import build_arbitrage_view, build_heatmap, build_top_n_view
from gui.components.controls import build_controls
from gui.components.table import build_rates_table
from utils.logger import get_logger

log = get_logger("gui")


def create_app() -> dash.Dash:
    app = dash.Dash(
        __name__,
        external_stylesheets=[
            dbc.themes.CYBORG,
            "https://fonts.googleapis.com/css2?family=Inter:wght@400;600&display=swap",
        ],
        title="Funding Rate Tool",
        update_title=None,
    )

    controls, interval = build_controls()

    app.layout = dbc.Container(
        [
            interval,
            dcc.Store(id="results-store"),

            html.Div([
                html.H2("Funding Rate Tool",
                        style={"color": DARK_THEME["text"], "margin": 0}),
                html.Div(id="last-updated",
                         style={"color": DARK_THEME["text_dim"], "fontSize": "13px"}),
            ], className="d-flex justify-content-between align-items-center my-3"),

            controls,

            dbc.Tabs(
                [
                    dbc.Tab(label="Funding Rates", tab_id="tab-rates"),
                    dbc.Tab(label="Arbitrage", tab_id="tab-arb"),
                    dbc.Tab(label="Heatmap", tab_id="tab-heatmap"),
                    dbc.Tab(label="Top-N", tab_id="tab-top"),
                ],
                id="tabs",
                active_tab="tab-rates",
                className="mt-3",
            ),

            # Top-N controls (visible only when the Top-N tab is active)
            html.Div(
                dbc.Card(dbc.CardBody(dbc.Row([
                    dbc.Col([
                        html.Label("Exchange", style={"color": DARK_THEME["text_dim"]}),
                        dcc.Dropdown(
                            id="top-exchange",
                            options=[{"label": e.title(), "value": e}
                                     for e in BULK_EXCHANGES],
                            value="binance",
                            clearable=False,
                            style={"color": "#000"},
                        ),
                    ], md=3),
                    dbc.Col([
                        html.Label("N", style={"color": DARK_THEME["text_dim"]}),
                        dcc.Input(id="top-n", type="number", value=20, min=1, max=200,
                                  step=5, debounce=True,
                                  style={"width": "100%",
                                         "backgroundColor": DARK_THEME["background"],
                                         "color": DARK_THEME["text"],
                                         "border": f"1px solid {DARK_THEME['border']}",
                                         "padding": "6px", "borderRadius": "6px"}),
                    ], md=2),
                    dbc.Col([
                        html.Label("Mode", style={"color": DARK_THEME["text_dim"]}),
                        dcc.Dropdown(
                            id="top-mode",
                            options=[
                                {"label": "Most extreme (|rate|)", "value": "abs"},
                                {"label": "Highest (most positive)", "value": "high"},
                                {"label": "Lowest (most negative)", "value": "low"},
                            ],
                            value="abs",
                            clearable=False,
                            style={"color": "#000"},
                        ),
                    ], md=4),
                ], className="g-3")), style={"backgroundColor": DARK_THEME["card"]}),
                id="top-controls",
                style={"display": "none", "marginTop": "12px"},
            ),

            html.Div(id="tab-content", className="mt-3"),
        ],
        fluid=True,
        style={
            "backgroundColor": DARK_THEME["background"],
            "color": DARK_THEME["text"],
            "minHeight": "100vh",
            "fontFamily": "Inter, sans-serif",
            "padding": "20px",
        },
    )

    @app.callback(
        Output("results-store", "data"),
        Output("last-updated", "children"),
        Input("refresh-btn", "n_clicks"),
        Input("auto-refresh", "n_intervals"),
        State("coin-selector", "value"),
        State("exchange-selector", "value"),
    )
    def refresh_data(_n_clicks, _n_intervals, coins, exchanges):
        coins = [c.upper() for c in (coins or [])]
        exchanges = list(exchanges or [])
        if not coins or not exchanges:
            return [], "select coins + exchanges"
        log.info("GUI refresh: coins=%s exchanges=%s", coins, exchanges)
        try:
            results = fetch_many_sync(coins, exchanges, use_cache=True)
        except Exception as e:
            log.exception("refresh failed: %s", e)
            return [], f"error: {e}"
        from datetime import datetime, timezone
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        return results, f"updated {stamp} — {len(results)} rows"

    @app.callback(
        Output("tab-content", "children"),
        Output("top-controls", "style"),
        Input("tabs", "active_tab"),
        Input("results-store", "data"),
        Input("alert-threshold", "value"),
        Input("top-exchange", "value"),
        Input("top-n", "value"),
        Input("top-mode", "value"),
        Input("auto-refresh", "n_intervals"),
        Input("refresh-btn", "n_clicks"),
    )
    def render_tab(active_tab, data, alert_bps,
                   top_exchange, top_n, top_mode, _i, _c):
        results: List[Dict] = data or []
        alert_bps = float(alert_bps or 0)
        hidden = {"display": "none", "marginTop": "12px"}
        shown = {"display": "block", "marginTop": "12px"}

        if active_tab == "tab-rates":
            return build_rates_table(results), hidden
        if active_tab == "tab-arb":
            return build_arbitrage_view(results, alert_bps=alert_bps), hidden
        if active_tab == "tab-heatmap":
            return build_heatmap(results), hidden
        if active_tab == "tab-top":
            n = int(top_n or 20)
            try:
                top_rows = fetch_top_n_sync(top_exchange, n=n, mode=top_mode or "abs")
            except Exception as e:
                log.exception("top-N fetch failed: %s", e)
                return dbc.Alert(f"Top-N fetch failed: {e}", color="danger"), shown
            return build_top_n_view(top_rows, top_exchange, top_mode or "abs"), shown
        return html.Div(), hidden

    return app


def run(host: str = GUI_HOST, port: int = GUI_PORT, debug: bool = False) -> None:
    app = create_app()
    print(f"Funding Rate Tool GUI -> http://{host}:{port}")
    app.run(host=host, port=port, debug=debug)


if __name__ == "__main__":
    run(debug=True)
