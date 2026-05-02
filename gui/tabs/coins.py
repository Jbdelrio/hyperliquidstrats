"""
coins.py — Tab 4: per-coin breakdown (PnL, spread dist, Hurst dist, skip reasons).
"""
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from gui.data_loader import load_decisions, load_fills, load_metrics
from gui.theme import COLORS, apply_dark_theme, no_data

_COINS = ["BTC", "ETH", "SOL", "HYPE", "AVAX", "LINK",
          "ARB", "OP", "AAVE", "LTC", "BCH", "XRP"]


def static_layout() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col([
                html.Label("Coin", style={"color": COLORS["text"]}),
                dcc.Dropdown(
                    id="coins-selector",
                    options=[{"label": c, "value": c} for c in _COINS],
                    value="BTC",
                    clearable=False,
                    style={"backgroundColor": COLORS["card_bg"], "color": "#000"},
                ),
            ], width=3),
        ], style={"marginBottom": "12px"}),
        html.Div(id="coins-content"),
    ])


def register_callbacks(app) -> None:
    @app.callback(
        Output("coins-content", "children"),
        Input("coins-selector",   "value"),
        Input("refresh-interval", "n_intervals"),
    )
    def update(coin, n):
        decisions = load_decisions()
        fills     = load_fills()
        metrics   = load_metrics()

        # ── PnL chart for this coin ─────────────────────────────────
        coin_fills = (
            fills[fills["symbol"] == coin].copy()
            if not fills.empty and "symbol" in fills.columns else None
        )
        if coin_fills is not None and not coin_fills.empty and "dt" in coin_fills.columns:
            coin_fills = coin_fills.sort_values("dt")
            coin_fills["cum_pnl"] = coin_fills["net"].cumsum()
            fig_pnl = px.line(coin_fills, x="dt", y="cum_pnl",
                              title=f"{coin} — PnL cumulé",
                              color_discrete_sequence=[COLORS["accent"]])
            apply_dark_theme(fig_pnl)
            pnl_chart = dcc.Graph(figure=fig_pnl, config={"displayModeBar": False})
        else:
            pnl_chart = no_data(f"Aucun fill pour {coin}.")

        if decisions.empty or "symbol" not in decisions.columns:
            return html.Div([pnl_chart, no_data(f"Aucune décision loggée pour {coin}.")])

        coin_dec = decisions[decisions["symbol"] == coin]
        if coin_dec.empty:
            return html.Div([pnl_chart, no_data(f"Aucune décision loggée pour {coin}.")])

        # ── Spread distribution ─────────────────────────────────────
        spread_chart = no_data("Spread non disponible.")
        if "spread_bps" in coin_dec.columns:
            spread_data = coin_dec["spread_bps"].dropna()
            if len(spread_data) > 0:
                fig_sp = px.histogram(
                    spread_data, x="spread_bps",
                    title=f"{coin} — Distribution du spread observé",
                    nbins=50,
                    color_discrete_sequence=[COLORS["accent"]],
                )
                fig_sp.add_vline(x=4.0, line_dash="dash",
                                 line_color=COLORS["success"],
                                 annotation_text="min 4 bps")
                fig_sp.add_vline(x=20.0, line_dash="dash",
                                 line_color=COLORS["danger"],
                                 annotation_text="max 20 bps")
                apply_dark_theme(fig_sp)
                spread_chart = dcc.Graph(figure=fig_sp, config={"displayModeBar": False})

        # ── Hurst distribution ──────────────────────────────────────
        hurst_chart = no_data("Hurst non disponible.")
        if "hurst" in coin_dec.columns:
            hurst_data = coin_dec["hurst"].dropna()
            if len(hurst_data) > 0:
                fig_h = px.histogram(
                    hurst_data, x="hurst",
                    title=f"{coin} — Distribution Hurst observé",
                    nbins=40,
                    color_discrete_sequence=[COLORS["warning"]],
                )
                fig_h.add_vline(x=0.5, line_dash="dot",
                                line_color=COLORS["text"],
                                annotation_text="H=0.5 (RW)")
                fig_h.add_vline(x=0.65, line_dash="dash",
                                line_color=COLORS["danger"],
                                annotation_text="TREND_HIGH")
                apply_dark_theme(fig_h)
                hurst_chart = dcc.Graph(figure=fig_h, config={"displayModeBar": False})

        # ── Top 5 skip reasons ──────────────────────────────────────
        skips = coin_dec[coin_dec["decision"] == "SKIP"]
        if not skips.empty:
            top = skips["reason"].value_counts().head(5).reset_index()
            top.columns = ["reason", "count"]
            fig_sk = px.bar(top, x="count", y="reason", orientation="h",
                            title=f"{coin} — Top 5 skip reasons",
                            color_discrete_sequence=[COLORS["danger"]])
            fig_sk.update_layout(yaxis={"categoryorder": "total ascending"})
            apply_dark_theme(fig_sk)
            skip_chart = dcc.Graph(figure=fig_sk, config={"displayModeBar": False})
        else:
            skip_chart = no_data(f"Aucun skip loggé pour {coin}.")

        return html.Div([
            dbc.Row([
                dbc.Col(pnl_chart, width=6),
                dbc.Col(skip_chart, width=6),
            ]),
            dbc.Row([
                dbc.Col(spread_chart, width=6),
                dbc.Col(hurst_chart,  width=6),
            ], style={"marginTop": "8px"}),
        ])
