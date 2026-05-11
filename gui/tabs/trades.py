"""
trades.py — Tab 3: fills table (pagination preserved) + PnL histogram.

DataTable is a STATIC component — only its data/columns/style are updated
on refresh so page_current is never reset.
"""
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, dash_table, dcc, html

from gui.data_loader import load_fills
from gui.theme import COLORS, apply_dark_theme, no_data

_ALL_COINS = ["ALL", "BTC", "ETH", "SOL", "HYPE", "AVAX", "LINK",
              "ARB", "OP", "AAVE", "LTC", "BCH", "XRP"]
_ALL_REASONS = ["ALL", "take_profit", "stop_loss", "max_hold",
                "z_reversion", "imbalance_reversed", "time_stop",
                "shutdown", "emergency", "flatten_strategy",
                "flatten_all", "manual_close"]
_ALL_STRATS  = ["ALL",
                "S8EMS", "MomentumLS", "BreakoutControlled",
                "MeanReversionKalman", "FundingArbitrage", "DonchianTrend",
                "RSIBollingerReversion", "RotationMomentum", "RelativeValue",
                "SpotPerpBasis", "FundingCarryHedged", "OBImbalanceScalper",
                "VolatilityRegimeBreakout", "MetaAlpha"]

_HDR = {"backgroundColor": "#1a1a1a", "color": COLORS["accent"], "fontWeight": "bold"}
_CEL = {"backgroundColor": COLORS["card_bg"], "color": COLORS["text"],
        "border": f"1px solid {COLORS['grid']}", "padding": "5px",
        "textAlign": "right", "fontSize": "0.82rem"}


def static_layout() -> html.Div:
    return html.Div([
        # ── Filters ────────────────────────────────────────────────────
        dbc.Row([
            dbc.Col([
                html.Label("Coin", style={"color": COLORS["text"], "fontSize": "11px"}),
                dcc.Dropdown(id="trades-coin-filter",
                             options=[{"label": c, "value": c} for c in _ALL_COINS],
                             value="ALL", clearable=False,
                             style={"backgroundColor": COLORS["card_bg"], "color": "#000"}),
            ], width=3),
            dbc.Col([
                html.Label("Stratégie", style={"color": COLORS["text"], "fontSize": "11px"}),
                dcc.Dropdown(id="trades-strat-filter",
                             options=[{"label": s, "value": s} for s in _ALL_STRATS],
                             value="ALL", clearable=False,
                             style={"backgroundColor": COLORS["card_bg"], "color": "#000"}),
            ], width=3),
            dbc.Col([
                html.Label("Exit reason", style={"color": COLORS["text"], "fontSize": "11px"}),
                dcc.Dropdown(id="trades-reason-filter",
                             options=[{"label": r, "value": r} for r in _ALL_REASONS],
                             value="ALL", clearable=False,
                             style={"backgroundColor": COLORS["card_bg"], "color": "#000"}),
            ], width=3),
            dbc.Col(
                html.Div(id="trades-count",
                         style={"color": COLORS["text"], "fontSize": "11px",
                                "paddingTop": "22px"}),
                width=3,
            ),
        ], style={"marginBottom": "12px"}),

        # ── Static DataTable — pagination preserved across refreshes ───
        dash_table.DataTable(
            id="trades-table",
            columns=[],
            data=[],
            page_size=30,
            page_action="native",
            sort_action="native",
            sort_mode="multi",
            style_table={"overflowX": "auto"},
            style_header=_HDR,
            style_cell=_CEL,
            style_cell_conditional=[
                {"if": {"column_id": c}, "textAlign": "left"}
                for c in ("ts", "symbol", "side", "reason", "strategy")
            ],
        ),

        # ── PnL histogram ──────────────────────────────────────────────
        html.Div(id="trades-hist-div", style={"marginTop": "16px"}),
    ])


def register_callbacks(app) -> None:

    @app.callback(
        Output("trades-table", "data"),
        Output("trades-table", "columns"),
        Output("trades-table", "style_data_conditional"),
        Output("trades-hist-div",  "children"),
        Output("trades-count",     "children"),
        Input("trades-coin-filter",   "value"),
        Input("trades-strat-filter",  "value"),
        Input("trades-reason-filter", "value"),
        Input("refresh-interval",     "n_intervals"),
    )
    def _update(coin_f, strat_f, reason_f, _n):
        df = load_fills()
        if df.empty:
            return [], [], [], no_data(), ""

        if coin_f   != "ALL" and "symbol"   in df.columns:
            df = df[df["symbol"]   == coin_f]
        if strat_f  != "ALL" and "strategy" in df.columns:
            df = df[df["strategy"] == strat_f]
        if reason_f != "ALL" and "reason"   in df.columns:
            df = df[df["reason"]   == reason_f]

        df = df.tail(500).iloc[::-1].reset_index(drop=True)   # newest first

        _COLS_ORDER = ["ts", "strategy", "symbol", "side", "notional",
                       "entry", "exit", "gross", "fee", "net", "hold_s", "reason"]
        display_cols = [c for c in _COLS_ORDER if c in df.columns]

        columns = [{"name": c, "id": c} for c in display_cols]
        records = df[display_cols].to_dict("records")

        # Row colouring based on net PnL
        style_cond = []
        if "net" in df.columns:
            for i, row in enumerate(records):
                net = row.get("net", 0) or 0
                style_cond.append({
                    "if": {"row_index": i},
                    "backgroundColor": "#002200" if net > 0 else "#220000",
                })

        # Histogram
        hist = no_data("Aucun trade filtré.")
        if "net" in df.columns and len(df) > 0:
            net_vals = df["net"].dropna().tolist()
            fig = go.Figure(go.Histogram(
                x=net_vals, nbinsx=30,
                marker_color=COLORS["accent"], opacity=0.8,
            ))
            fig.add_vline(x=0, line_dash="dash", line_color=COLORS["danger"], opacity=0.7)
            fig.update_layout(title="Distribution PnL net (USD)",
                              bargap=0.05, height=220)
            apply_dark_theme(fig)
            hist = dcc.Graph(figure=fig, config={"displayModeBar": False})

        total = len(df)
        wins  = int((df["net"] > 0).sum()) if "net" in df.columns else 0
        count_txt = f"{total} trades  |  {wins}W / {total-wins}L"

        return records, columns, style_cond, hist, count_txt
