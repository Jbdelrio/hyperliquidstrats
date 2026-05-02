"""
overview.py — Tab 1: equity curve, PnL summary cards, kill-switch badge.
"""
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from gui.data_loader import load_fills, load_metrics
from gui.theme import COLORS, apply_dark_theme, no_data, stat_card

INITIAL_EQUITY = 500.0


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="overview-cards"),
        html.Div(id="overview-charts", style={"marginTop": "16px"}),
    ])


def register_callbacks(app) -> None:
    @app.callback(
        Output("overview-cards",  "children"),
        Output("overview-charts", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def update(n):
        metrics = load_metrics()
        fills   = load_fills()

        # ── Cards ──────────────────────────────────────────────────
        if metrics.empty:
            cards = no_data("En attente des métriques (metrics_v9/metrics_v9.csv)...")
            charts = no_data()
            return cards, charts

        last = metrics.iloc[-1]
        equity  = last.get("equity",   INITIAL_EQUITY)
        pnl_day = last.get("pnl_day",  0.0)
        pnl_1h  = last.get("pnl_hour", 0.0)
        wins    = int(last.get("wins",    0))
        losses  = int(last.get("losses",  0))
        total   = wins + losses
        wr      = f"{100 * wins / total:.1f}%" if total > 0 else "—"

        dd_daily = (equity - INITIAL_EQUITY) / INITIAL_EQUITY * 100
        dd_color = COLORS["danger"] if dd_daily < -1 else COLORS["success"]

        pnl_day_color = COLORS["success"] if pnl_day >= 0 else COLORS["danger"]
        pnl_1h_color  = COLORS["success"] if pnl_1h  >= 0 else COLORS["danger"]

        cards = dbc.Row([
            dbc.Col(stat_card("Equity",       f"${equity:.2f}",         COLORS["accent"])),
            dbc.Col(stat_card("PnL today",    f"${pnl_day:+.4f}",       pnl_day_color)),
            dbc.Col(stat_card("PnL 1h",       f"${pnl_1h:+.4f}",        pnl_1h_color)),
            dbc.Col(stat_card("Daily P&L %",  f"{dd_daily:+.2f}%",      dd_color)),
            dbc.Col(stat_card("Trades",       str(total),               COLORS["text_light"])),
            dbc.Col(stat_card("Win rate",     wr,                       COLORS["accent"])),
        ], className="g-2")

        # ── Kill switch badge ───────────────────────────────────────
        daily_dd_pct = abs(pnl_day / INITIAL_EQUITY) if INITIAL_EQUITY else 0
        ks_color  = COLORS["danger"] if daily_dd_pct > 0.03 else COLORS["success"]
        ks_label  = "SUSPENDED (daily DD)" if daily_dd_pct > 0.03 else "ACTIVE"
        ks_badge  = dbc.Badge(ks_label, style={"backgroundColor": ks_color,
                                               "marginLeft": "8px"})

        # ── Equity curve ────────────────────────────────────────────
        if "dt" in metrics.columns and "equity" in metrics.columns:
            fig_eq = px.line(metrics, x="dt", y="equity",
                             title="Equity curve",
                             color_discrete_sequence=[COLORS["accent"]])
            fig_eq.add_hline(y=INITIAL_EQUITY, line_dash="dash",
                             line_color=COLORS["warning"], opacity=0.5)
            apply_dark_theme(fig_eq)
        else:
            fig_eq = go.Figure()
            apply_dark_theme(fig_eq)

        # ── PnL by coin (bar) ───────────────────────────────────────
        fills_chart = no_data("Aucun fill pour le moment.")
        if not fills.empty and "symbol" in fills.columns and "net" in fills.columns:
            coin_pnl = fills.groupby("symbol")["net"].sum().reset_index()
            coin_pnl.columns = ["symbol", "net_pnl"]
            coin_pnl = coin_pnl.sort_values("net_pnl")
            fig_coins = px.bar(
                coin_pnl, x="net_pnl", y="symbol", orientation="h",
                title="PnL cumulé par coin",
                color="net_pnl",
                color_continuous_scale=["#CC0000", "#333333", "#77B300"],
            )
            fig_coins.update_coloraxes(showscale=False)
            apply_dark_theme(fig_coins)
            fills_chart = dcc.Graph(figure=fig_coins, config={"displayModeBar": False})

        charts = html.Div([
            dbc.Row([
                dbc.Col([
                    html.Div([
                        html.Span("Kill switch", style={"color": COLORS["text"]}),
                        ks_badge,
                    ], style={"marginBottom": "8px"}),
                    dcc.Graph(figure=fig_eq, config={"displayModeBar": False}),
                ], width=8),
                dbc.Col(fills_chart, width=4),
            ]),
        ])

        return cards, charts
