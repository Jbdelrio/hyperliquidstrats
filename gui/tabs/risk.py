"""
risk.py — Tab 5: drawdown gauges + time series + loss history.
"""
import time

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import ALL, Input, Output, ctx, dash_table, dcc, html

from gui.control_api import ControlAPI
from gui.data_loader import load_fills, load_metrics, load_strategy_status
from gui.theme import COLORS, apply_dark_theme, gauge_fig, no_data, stat_card

DAILY_DD_LIMIT  = 0.030
TOTAL_DD_LIMIT  = 0.060
_DFLT_TOTAL_CAP = 7000.0   # 14 strats × $500

_api = ControlAPI()


def _get_init_cap() -> float:
    live = load_strategy_status()
    if live:
        total = sum(float(s.get("capital_allocated_usd", 0) or 0) for s in live)
        if total > 0:
            return total
    return _DFLT_TOTAL_CAP


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="risk-content"),
        dcc.Store(id="risk-reactivate-store"),
    ])


def register_callbacks(app) -> None:

    @app.callback(
        Output("risk-content", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def update(_n):
        metrics = load_metrics()
        fills   = load_fills()

        if metrics.empty:
            return no_data("En attente de metrics_v9/metrics_v9.csv...")

        init_cap = _get_init_cap()

        last   = metrics.iloc[-1]
        equity = float(last.get("equity", init_cap))

        pnl_day  = float(last.get("pnl_day",  0.0))
        dd_daily = abs(min(pnl_day, 0.0)) / init_cap * 100   # %
        dd_total = abs(min(equity - init_cap, 0.0)) / init_cap * 100

        dd_d_color = COLORS["danger"] if dd_daily > DAILY_DD_LIMIT * 100 * 0.7 else COLORS["success"]
        dd_t_color = COLORS["danger"] if dd_total > TOTAL_DD_LIMIT * 100 * 0.7 else COLORS["success"]

        # ── KPI cards ────────────────────────────────────────────────
        wins   = int(last.get("wins",   0) or 0)
        losses = int(last.get("losses", 0) or 0)
        total  = wins + losses
        wr_txt = f"{100*wins/total:.1f}%" if total else "—"
        streak = int(last.get("avg_hold_s", 0) or 0)   # proxy — actual streak not in metrics

        kpi_row = dbc.Row([
            dbc.Col(stat_card("Daily DD",
                              f"{dd_daily:.2f}% / {DAILY_DD_LIMIT*100:.1f}%",
                              dd_d_color)),
            dbc.Col(stat_card("Total DD",
                              f"{dd_total:.2f}% / {TOTAL_DD_LIMIT*100:.1f}%",
                              dd_t_color)),
            dbc.Col(stat_card("Equity",   f"${equity:.2f}", COLORS["accent"])),
            dbc.Col(stat_card("Win rate", wr_txt, COLORS["accent"])),
            dbc.Col(stat_card("Trades",   str(total), COLORS["text_light"])),
        ], className="g-2")

        # ── Gauge row ─────────────────────────────────────────────────
        fig_g_daily = gauge_fig(
            value=dd_daily, min_val=0, max_val=DAILY_DD_LIMIT * 100 * 2,
            title="Daily DD %",
            warn_pct=0.5, danger_pct=DAILY_DD_LIMIT * 100 / (DAILY_DD_LIMIT * 100 * 2),
            unit="%", height=185,
        )
        fig_g_total = gauge_fig(
            value=dd_total, min_val=0, max_val=TOTAL_DD_LIMIT * 100 * 2,
            title="Total DD %",
            warn_pct=0.5, danger_pct=TOTAL_DD_LIMIT * 100 / (TOTAL_DD_LIMIT * 100 * 2),
            unit="%", height=185,
        )
        gauge_row = dbc.Row([
            dbc.Col(dcc.Graph(figure=fig_g_daily,
                              config={"displayModeBar": False}), width=3),
            dbc.Col(dcc.Graph(figure=fig_g_total,
                              config={"displayModeBar": False}), width=3),
            dbc.Col(_equity_diff_chart(metrics), width=6),
        ], className="g-2", style={"marginTop": "16px"})

        # ── Drawdown time series ──────────────────────────────────────
        dd_chart = no_data("Pas assez de données temporelles.")
        if "dt" in metrics.columns and "pnl_day" in metrics.columns:
            mdf = metrics.copy()
            mdf["daily_dd_pct"] = mdf["pnl_day"].apply(
                lambda x: abs(min(float(x) if x == x else 0.0, 0.0)) / init_cap * 100
            )
            mdf["total_dd_pct"] = (
                (init_cap - mdf["equity"]).clip(lower=0) / init_cap * 100
            )

            fig = go.Figure()
            # Zone rouge > danger threshold
            fig.add_hrect(y0=DAILY_DD_LIMIT * 100, y1=99,
                          fillcolor="rgba(204,0,0,0.06)", line_width=0)
            # Zone orange > warn threshold
            fig.add_hrect(y0=DAILY_DD_LIMIT * 100 * 0.7, y1=DAILY_DD_LIMIT * 100,
                          fillcolor="rgba(255,136,0,0.05)", line_width=0)

            fig.add_trace(go.Scatter(
                x=mdf["dt"], y=mdf["daily_dd_pct"],
                name="Daily DD %", line=dict(color=COLORS["warning"], width=2),
                fill="tozeroy", fillcolor="rgba(255,136,0,0.06)",
            ))
            fig.add_trace(go.Scatter(
                x=mdf["dt"], y=mdf["total_dd_pct"],
                name="Total DD %", line=dict(color=COLORS["danger"], width=2),
            ))
            fig.add_hline(y=DAILY_DD_LIMIT * 100, line_dash="dash",
                          line_color=COLORS["warning"], opacity=0.6,
                          annotation_text=f"Daily limit {DAILY_DD_LIMIT*100:.1f}%",
                          annotation_font_color=COLORS["warning"])
            fig.add_hline(y=TOTAL_DD_LIMIT * 100, line_dash="dash",
                          line_color=COLORS["danger"], opacity=0.6,
                          annotation_text=f"Total limit {TOTAL_DD_LIMIT*100:.1f}%",
                          annotation_font_color=COLORS["danger"])
            fig.update_layout(title="Drawdown au fil du temps (%)",
                              yaxis_title="%", height=240)
            apply_dark_theme(fig)
            dd_chart = dcc.Graph(figure=fig, config={"displayModeBar": False})

        # ── Loss history table ────────────────────────────────────────
        loss_section = no_data("Historique des pertes non disponible.")
        if not fills.empty and "net" in fills.columns and "ts" in fills.columns:
            loss_rows = fills[fills["net"] < 0][["ts", "symbol", "net", "reason",
                                                  "strategy"]].tail(20)
            if not loss_rows.empty:
                loss_rows = loss_rows.copy()
                loss_rows["net"] = loss_rows["net"].apply(lambda x: f"${x:.4f}")
                table = dash_table.DataTable(
                    data=loss_rows.to_dict("records"),
                    columns=[{"name": c, "id": c} for c in loss_rows.columns],
                    style_header={"backgroundColor": "#1a1a1a",
                                  "color": COLORS["danger"], "fontWeight": "bold",
                                  "fontSize": "11px"},
                    style_cell={"backgroundColor": "#180000",
                                "color": COLORS["text"],
                                "border": f"1px solid {COLORS['grid']}",
                                "padding": "4px 8px", "fontSize": "11px",
                                "fontFamily": "Consolas, monospace"},
                    style_table={"overflowX": "auto"},
                )
                loss_section = html.Div([
                    html.H6("20 dernières pertes",
                            style={"color": COLORS["danger"], "marginTop": "12px",
                                   "fontSize": "11px", "letterSpacing": "1px"}),
                    table,
                ])

        # ── Suspended strategies panel ────────────────────────────────
        susp_section = _suspended_panel(load_strategy_status())

        return html.Div([
            kpi_row,
            gauge_row,
            html.Div(dd_chart,      style={"marginTop": "16px"}),
            html.Div(loss_section,  style={"marginTop": "16px"}),
            html.Div(susp_section,  style={"marginTop": "16px"}),
        ])

    @app.callback(
        Output("risk-reactivate-store", "data"),
        Input({"type": "btn-reactivate", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def reactivate_strategy(n_clicks_list):
        if not any(n for n in n_clicks_list if n):
            return {}
        triggered = ctx.triggered_id
        if not triggered or not isinstance(triggered, dict):
            return {}
        name = triggered.get("index")
        if name:
            _api.enable_strategy(name)
        return {"reactivated": name, "ts": time.time()}


def _suspended_panel(live_list: list) -> html.Div:
    """Panel listing currently suspended strategies with a Réactiver button each."""
    if not live_list:
        return html.Div()

    now = time.time()
    suspended = [s for s in live_list
                 if float(s.get("suspended_until", 0) or 0) > now]

    if not suspended:
        return html.Div()

    rows = []
    for s in suspended:
        name  = s.get("name", "?")
        until = float(s.get("suspended_until", 0) or 0)
        remaining_s = max(0, until - now)
        if remaining_s > 3600:
            eta = f"{remaining_s/3600:.1f}h"
        elif remaining_s > 60:
            eta = f"{int(remaining_s/60)}min"
        else:
            eta = f"{int(remaining_s)}s"

        rows.append(dbc.Row([
            dbc.Col(html.Span(name,  style={"color": COLORS["text"],
                                            "fontFamily": "Consolas, monospace",
                                            "fontSize": "12px"}), width=6),
            dbc.Col(html.Span(f"reprend dans {eta}",
                              style={"color": COLORS["warning"], "fontSize": "11px"}),
                    width=4),
            dbc.Col(dbc.Button("Réactiver",
                               id={"type": "btn-reactivate", "index": name},
                               size="sm", color="warning", outline=True,
                               style={"fontSize": "10px", "padding": "2px 8px"}),
                    width=2),
        ], className="g-1 align-items-center", style={"marginBottom": "4px"}))

    return html.Div([
        html.H6("Stratégies suspendues",
                style={"color": COLORS["warning"], "fontSize": "11px",
                       "letterSpacing": "1px", "marginBottom": "8px"}),
        html.Div(rows,
                 style={"backgroundColor": "#1a1200", "borderRadius": "4px",
                        "padding": "8px 12px",
                        "border": f"1px solid {COLORS['warning']}33"}),
    ])


def _equity_diff_chart(metrics):
    """Mini hourly PnL bar chart."""
    if metrics.empty or "dt" not in metrics.columns or "pnl_min" not in metrics.columns:
        return html.Div()
    fig = go.Figure()
    pnl_vals = metrics["pnl_min"].fillna(0).tolist()
    fig.add_trace(go.Bar(
        x=metrics["dt"], y=pnl_vals,
        marker_color=[COLORS["success"] if v >= 0 else COLORS["danger"]
                      for v in pnl_vals],
        name="PnL/min",
    ))
    fig.update_layout(title="PnL par minute", showlegend=False, height=185)
    apply_dark_theme(fig)
    return dcc.Graph(figure=fig, config={"displayModeBar": False})
