"""
trades.py — Tab 3: fills table with filters, net PnL histogram.
"""
import dash_bootstrap_components as dbc
import plotly.express as px
from dash import Input, Output, dash_table, dcc, html

from gui.data_loader import load_fills
from gui.theme import COLORS, apply_dark_theme, no_data

_ALL_COINS   = ["ALL", "BTC", "ETH", "SOL", "HYPE", "AVAX", "LINK",
                "ARB", "OP", "AAVE", "LTC", "BCH", "XRP"]
_ALL_REASONS = ["ALL", "take_profit", "stop_loss", "max_hold", "shutdown", "emergency"]


def static_layout() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col([
                html.Label("Coin", style={"color": COLORS["text"]}),
                dcc.Dropdown(
                    id="trades-coin-filter",
                    options=[{"label": c, "value": c} for c in _ALL_COINS],
                    value="ALL",
                    clearable=False,
                    style={"backgroundColor": COLORS["card_bg"], "color": "#000"},
                ),
            ], width=3),
            dbc.Col([
                html.Label("Exit reason", style={"color": COLORS["text"]}),
                dcc.Dropdown(
                    id="trades-reason-filter",
                    options=[{"label": r, "value": r} for r in _ALL_REASONS],
                    value="ALL",
                    clearable=False,
                    style={"backgroundColor": COLORS["card_bg"], "color": "#000"},
                ),
            ], width=3),
        ], style={"marginBottom": "12px"}),
        html.Div(id="trades-content"),
    ])


def register_callbacks(app) -> None:
    @app.callback(
        Output("trades-content", "children"),
        Input("trades-coin-filter",   "value"),
        Input("trades-reason-filter", "value"),
        Input("refresh-interval",     "n_intervals"),
    )
    def update(coin_filter, reason_filter, n):
        df = load_fills()
        if df.empty:
            return no_data()

        if coin_filter   != "ALL" and "symbol" in df.columns:
            df = df[df["symbol"] == coin_filter]
        if reason_filter != "ALL" and "reason" in df.columns:
            df = df[df["reason"] == reason_filter]

        df = df.tail(100)

        # Style: net_pnl > 0 green, < 0 red
        style_cond = []
        if "net" in df.columns:
            for i, (_, row) in enumerate(df.iterrows()):
                net = row.get("net", 0)
                bg = "#002200" if net > 0 else "#220000"
                style_cond.append({"if": {"row_index": i}, "backgroundColor": bg})

        display_cols = [c for c in ["ts", "symbol", "side", "notional",
                                     "entry", "exit", "gross", "fee", "net",
                                     "hold_s", "reason"]
                        if c in df.columns]

        table = dash_table.DataTable(
            data=df[display_cols].to_dict("records"),
            columns=[{"name": c, "id": c} for c in display_cols],
            style_table={"overflowX": "auto"},
            style_header={"backgroundColor": "#1a1a1a", "color": COLORS["accent"],
                          "fontWeight": "bold"},
            style_cell={"backgroundColor": COLORS["card_bg"], "color": COLORS["text"],
                        "border": f"1px solid {COLORS['grid']}", "padding": "5px",
                        "textAlign": "right", "fontSize": "0.82rem"},
            style_cell_conditional=[
                {"if": {"column_id": c}, "textAlign": "left"}
                for c in ("ts", "symbol", "side", "reason")
            ],
            style_data_conditional=style_cond,
            page_size=30,
        )

        hist = no_data("Aucun trade filtré.")
        if "net" in df.columns and len(df) > 0:
            fig = px.histogram(
                df, x="net", nbins=30,
                title="Distribution PnL net (USD)",
                color_discrete_sequence=[COLORS["accent"]],
            )
            fig.add_vline(x=0, line_dash="dash", line_color=COLORS["danger"])
            apply_dark_theme(fig)
            hist = dcc.Graph(figure=fig, config={"displayModeBar": False})

        return html.Div([table, html.Div(hist, style={"marginTop": "16px"})])
