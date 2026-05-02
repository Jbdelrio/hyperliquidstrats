"""
risk.py — Tab 5: drawdown chart, kill-switch state, suspend history.
"""
import dash_bootstrap_components as dbc
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, dash_table, dcc, html

from gui.data_loader import load_fills, load_metrics
from gui.theme import COLORS, apply_dark_theme, no_data, stat_card

INITIAL_EQUITY = 500.0
DAILY_DD_LIMIT = 0.030
TOTAL_DD_LIMIT = 0.060


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="risk-content"),
    ])


def register_callbacks(app) -> None:
    @app.callback(
        Output("risk-content", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def update(n):
        metrics = load_metrics()
        fills   = load_fills()

        if metrics.empty:
            return no_data("En attente de metrics_v9/metrics_v9.csv...")

        last = metrics.iloc[-1]
        equity = float(last.get("equity", INITIAL_EQUITY))

        # ── Drawdown gauge cards ────────────────────────────────────
        pnl_day   = float(last.get("pnl_day", 0.0))
        dd_daily  = abs(min(pnl_day, 0.0)) / INITIAL_EQUITY
        dd_total  = abs(min(equity - INITIAL_EQUITY, 0.0)) / INITIAL_EQUITY

        dd_d_color = COLORS["danger"] if dd_daily > DAILY_DD_LIMIT * 0.7 else COLORS["success"]
        dd_t_color = COLORS["danger"] if dd_total > TOTAL_DD_LIMIT * 0.7 else COLORS["success"]

        cards = dbc.Row([
            dbc.Col(stat_card("Daily drawdown",
                              f"{dd_daily * 100:.2f}% / {DAILY_DD_LIMIT*100:.1f}%",
                              dd_d_color)),
            dbc.Col(stat_card("Total drawdown",
                              f"{dd_total * 100:.2f}% / {TOTAL_DD_LIMIT*100:.1f}%",
                              dd_t_color)),
            dbc.Col(stat_card("Equity",
                              f"${equity:.2f}",
                              COLORS["accent"])),
            dbc.Col(stat_card("KS state",
                              "no data",
                              COLORS["text"])),
        ], className="g-2")

        # ── Drawdown chart over time ────────────────────────────────
        charts = no_data("Pas assez de données temporelles.")
        if "dt" in metrics.columns and "pnl_day" in metrics.columns:
            mdf = metrics.copy()
            mdf["daily_dd_pct"] = mdf["pnl_day"].apply(
                lambda x: abs(min(float(x) if x == x else 0.0, 0.0)) / INITIAL_EQUITY * 100
            )
            mdf["total_dd_pct"] = (INITIAL_EQUITY - mdf["equity"]).clip(lower=0) / INITIAL_EQUITY * 100

            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=mdf["dt"], y=mdf["daily_dd_pct"],
                name="Daily DD %", line=dict(color=COLORS["warning"]),
            ))
            fig.add_trace(go.Scatter(
                x=mdf["dt"], y=mdf["total_dd_pct"],
                name="Total DD %", line=dict(color=COLORS["danger"]),
            ))
            fig.add_hline(y=DAILY_DD_LIMIT * 100, line_dash="dash",
                          line_color=COLORS["warning"], opacity=0.5)
            fig.add_hline(y=TOTAL_DD_LIMIT * 100, line_dash="dash",
                          line_color=COLORS["danger"], opacity=0.5)
            fig.update_layout(title="Drawdown au fil du temps (%)")
            apply_dark_theme(fig)
            charts = dcc.Graph(figure=fig, config={"displayModeBar": False})

        # ── Suspend history from fills ──────────────────────────────
        suspend_section = no_data("Historique des suspensions non disponible dans le CSV.")

        # Detect max_hold run (proxy for suspense: 4+ consecutive losses)
        if not fills.empty and "net" in fills.columns and "ts" in fills.columns:
            loss_rows = fills[fills["net"] < 0][["ts", "symbol", "net", "reason"]]
            if not loss_rows.empty:
                table = dash_table.DataTable(
                    data=loss_rows.tail(20).to_dict("records"),
                    columns=[{"name": c, "id": c} for c in loss_rows.columns],
                    style_header={"backgroundColor": "#1a1a1a", "color": COLORS["accent"],
                                  "fontWeight": "bold"},
                    style_cell={"backgroundColor": "#220000", "color": COLORS["text"],
                                "border": f"1px solid {COLORS['grid']}", "padding": "5px"},
                    style_table={"overflowX": "auto"},
                )
                suspend_section = html.Div([
                    html.H6("20 dernières pertes (déclencheurs potentiels de streak)",
                            style={"color": COLORS["danger"], "marginTop": "12px"}),
                    table,
                ])

        return html.Div([
            cards,
            html.Div(charts,          style={"marginTop": "16px"}),
            html.Div(suspend_section, style={"marginTop": "16px"}),
        ])
