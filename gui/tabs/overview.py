"""
overview.py — Tab 1: per-strategy PnL cards + equity curve + live positions.
Outputs kept at original IDs (overview-cards / overview-charts) for Dash cache compat.
"""
import time

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from gui.data_loader import load_fills, load_metrics, load_strategy_status
from gui.theme import COLORS, apply_dark_theme, no_data, stat_card

INITIAL_EQUITY = 500.0
_STRATS = ["S8EMS", "MomentumLS", "BreakoutControlled",
           "MeanReversionKalman", "FundingArbitrage"]
_DFLT_CAP = {"S8EMS": 100, "MomentumLS": 150, "BreakoutControlled": 100,
             "MeanReversionKalman": 100, "FundingArbitrage": 50}

_BDR = f"1px solid {COLORS['grid']}"
_TD  = {"border": _BDR, "padding": "4px 8px", "fontSize": "11px",
        "fontFamily": "Consolas, monospace", "color": COLORS["text_light"]}
_TH2 = {"backgroundColor": "#060606", "color": COLORS["accent"],
        "fontWeight": "bold", "fontSize": "10px",
        "border": _BDR, "padding": "4px 8px", "letterSpacing": "1px"}


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="overview-cards"),
        html.Div(id="overview-charts", style={"marginTop": "16px"}),
    ])


def _strat_card(name, status, cap, cum_pnl, n_trades, wr, n_pos):
    sc = {"ACTIF": COLORS["success"], "INACTIF": COLORS["danger"],
          "SUSPENDU": COLORS["warning"]}.get(status, COLORS["text"])
    pnl_txt   = f"${cum_pnl:+.2f}" if cum_pnl is not None else "N/A"
    pnl_color = (COLORS["success"] if (cum_pnl or 0) >= 0 else COLORS["danger"]) if cum_pnl is not None else COLORS["text"]
    wr_txt    = f"{wr:.0f}%" if wr is not None else "—"

    return dbc.Card(dbc.CardBody([
        dbc.Row([
            dbc.Col(html.B(name, style={"color": COLORS["accent"], "fontSize": "12px"}), width="auto"),
            dbc.Col(dbc.Badge(status, style={"backgroundColor": sc, "fontSize": "9px",
                                             "padding": "2px 6px"}), width="auto"),
            dbc.Col(width=True),
            dbc.Col(html.Span(f"${cap:.0f}", style={"color": COLORS["text"],
                                                     "fontSize": "11px",
                                                     "fontFamily": "Consolas,monospace"}), width="auto"),
        ], className="g-1 align-items-center mb-1"),
        dbc.Row([
            dbc.Col([html.Div("PnL", style={"color": COLORS["text"], "fontSize": "9px"}),
                     html.Div(pnl_txt, style={"color": pnl_color, "fontWeight": "700",
                                               "fontSize": "14px", "fontFamily": "Consolas,monospace"})], width=4),
            dbc.Col([html.Div("Trades/WR", style={"color": COLORS["text"], "fontSize": "9px"}),
                     html.Div(f"{n_trades}/{wr_txt}", style={"color": COLORS["text_light"],
                                                              "fontWeight": "700", "fontSize": "12px",
                                                              "fontFamily": "Consolas,monospace"})], width=4),
            dbc.Col([html.Div("Pos", style={"color": COLORS["text"], "fontSize": "9px"}),
                     html.Div(str(n_pos), style={"color": COLORS["warning"] if n_pos else COLORS["text"],
                                                  "fontWeight": "700", "fontSize": "14px",
                                                  "fontFamily": "Consolas,monospace"})], width=4),
        ]),
    ], style={"padding": "8px 10px"}),
    style={"backgroundColor": COLORS["card_bg"], "border": _BDR, "borderRadius": "4px"})


def _pos_table(positions):
    if not positions:
        return html.Div()
    rows = []
    for p in positions:
        upnl = p.get("unrealized_pnl", 0.0)
        uc   = COLORS["success"] if upnl >= 0 else COLORS["danger"]
        sc   = COLORS["success"] if p.get("side") == "BUY" else COLORS["danger"]
        rows.append(html.Tr([
            html.Td(p.get("strategy", "?"), style={**_TD, "color": COLORS["warning"]}),
            html.Td(p.get("symbol",   "?"), style={**_TD, "color": COLORS["accent"], "fontWeight": "700"}),
            html.Td(p.get("side",     "?"), style={**_TD, "color": sc, "fontWeight": "700"}),
            html.Td(f"${p.get('notional_usd', 0):.0f}",   style=_TD),
            html.Td(f"{p.get('entry_price', 0):.5g}",     style=_TD),
            html.Td(f"{p.get('current_price', 0):.5g}",   style=_TD),
            html.Td(f"${upnl:+.4f}", style={**_TD, "color": uc, "fontWeight": "700"}),
            html.Td(f"{p.get('hold_s', 0)}s",             style=_TD),
        ]))
    thead = html.Thead(html.Tr(
        [html.Th(h, style=_TH2) for h in ["STRAT","COIN","SIDE","NOTIO","ENTRY","MID","UPNL","HOLD"]]
    ))
    return html.Div([
        html.P("POSITIONS OUVERTES", style={"color": COLORS["warning"], "letterSpacing": "2px",
                                            "fontSize": "10px", "marginBottom": "4px",
                                            "fontWeight": "700"}),
        html.Table([thead, html.Tbody(rows)],
                   style={"width": "100%", "borderCollapse": "collapse"}),
    ], style={"backgroundColor": COLORS["card_bg"], "border": _BDR,
              "borderRadius": "4px", "padding": "8px 12px", "marginTop": "10px"})


def register_callbacks(app) -> None:

    @app.callback(
        Output("overview-cards",  "children"),
        Output("overview-charts", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def update(_n):
        fills     = load_fills()
        metrics   = load_metrics()
        live_list = load_strategy_status()
        live_map  = {s.get("name"): s for s in live_list}
        now       = time.time()

        # Per-strategy stats from fills
        strat_stats: dict = {}
        if not fills.empty and "net" in fills.columns and "strategy" in fills.columns:
            for sname, g in fills.groupby("strategy"):
                if not sname:
                    continue
                strat_stats[sname] = {
                    "pnl": float(g["net"].sum()),
                    "n":   len(g),
                    "wr":  100.0 * (g["net"] > 0).sum() / len(g),
                }

        # Global metrics
        if not metrics.empty:
            last    = metrics.iloc[-1]
            equity  = float(last.get("equity",   INITIAL_EQUITY) or INITIAL_EQUITY)
            pnl_day = float(last.get("pnl_day",  0) or 0)
            pnl_1h  = float(last.get("pnl_hour", 0) or 0)
            wins    = int(last.get("wins",   0) or 0)
            losses  = int(last.get("losses", 0) or 0)
            total   = wins + losses
            wr_txt  = f"{100*wins/total:.1f}%" if total else "—"
            dd      = (equity - INITIAL_EQUITY) / INITIAL_EQUITY * 100
        else:
            equity = INITIAL_EQUITY; pnl_day = pnl_1h = 0.0
            total = wins = 0; wr_txt = "—"; dd = 0.0

        # All open positions
        all_pos = []
        for s in live_list:
            for p in s.get("open_positions", []):
                p["strategy"] = s.get("name", "?")
                all_pos.append(p)

<<<<<<< HEAD
        # ── Global summary cards ─────────────────────────────────────
        g_cards = dbc.Row([
            dbc.Col(stat_card("Equity",      f"${equity:.2f}", COLORS["accent"])),
            dbc.Col(stat_card("PnL today",   f"${pnl_day:+.2f}",
                              COLORS["success"] if pnl_day >= 0 else COLORS["danger"])),
            dbc.Col(stat_card("PnL 1h",      f"${pnl_1h:+.2f}",
                              COLORS["success"] if pnl_1h >= 0 else COLORS["danger"])),
            dbc.Col(stat_card("DD",          f"{dd:+.2f}%",
                              COLORS["danger"] if dd < -1 else COLORS["success"])),
            dbc.Col(stat_card("Trades",      str(total), COLORS["text_light"])),
            dbc.Col(stat_card("Win rate",    wr_txt,     COLORS["accent"])),
            dbc.Col(stat_card("Positions",   str(len(all_pos)),
                              COLORS["warning"] if all_pos else COLORS["text"])),
=======
        pnl_day_color = COLORS["success"] if pnl_day >= 0 else COLORS["danger"]
        pnl_1h_color  = COLORS["success"] if pnl_1h  >= 0 else COLORS["danger"]

        cards = dbc.Row([
            dbc.Col(stat_card("Equity",       f"${equity:.2f}",         COLORS["accent"])),
            dbc.Col(stat_card("PnL today",    f"${pnl_day:+.2f}",       pnl_day_color)),
            dbc.Col(stat_card("PnL 1h",       f"${pnl_1h:+.2f}",        pnl_1h_color)),
            dbc.Col(stat_card("Daily P&L %",  f"{dd_daily:+.2f}%",      dd_color)),
            dbc.Col(stat_card("Trades",       str(total),               COLORS["text_light"])),
            dbc.Col(stat_card("Win rate",     wr,                       COLORS["accent"])),
>>>>>>> 413da59b759b43b2ccd82bb9fd2a13ffbeccdf9d
        ], className="g-2")

        # ── Per-strategy cards ───────────────────────────────────────
        s_cols = []
        for name in _STRATS:
            live  = live_map.get(name, {})
            cap   = float(live.get("capital_allocated_usd", _DFLT_CAP.get(name, 100)) or _DFLT_CAP.get(name, 100))
            ena   = live.get("enabled", False) if live else False
            susp  = float(live.get("suspended_until", 0) or 0)
            status = "SUSPENDU" if susp > now else ("ACTIF" if ena else "INACTIF")
            n_pos  = len(live.get("open_positions", [])) if live else 0
            st    = strat_stats.get(name, {})
            s_cols.append(dbc.Col(
                _strat_card(name, status, cap,
                            st.get("pnl"), st.get("n", 0), st.get("wr"), n_pos),
                width=True))
        strat_row = dbc.Row(s_cols, className="g-2", style={"marginTop": "10px"})

        pos_div = _pos_table(all_pos)

        cards = html.Div([g_cards, strat_row, pos_div])

        # ── Charts ───────────────────────────────────────────────────
        fig_eq = go.Figure()
        if not metrics.empty and "dt" in metrics.columns and "equity" in metrics.columns:
            fig_eq.add_trace(go.Scatter(x=metrics["dt"], y=metrics["equity"],
                                        mode="lines", name="Equity",
                                        line=dict(color=COLORS["accent"], width=2)))
            fig_eq.add_hline(y=INITIAL_EQUITY, line_dash="dash",
                             line_color=COLORS["warning"], opacity=0.5)
        fig_eq.update_layout(title="Equity curve", showlegend=False, height=280)
        apply_dark_theme(fig_eq)

        fig_strat = go.Figure()
        if strat_stats:
            names = list(strat_stats.keys())
            pnls  = [strat_stats[n]["pnl"] for n in names]
            fig_strat.add_trace(go.Bar(
                x=pnls, y=names, orientation="h",
                marker_color=[COLORS["success"] if p >= 0 else COLORS["danger"] for p in pnls],
                text=[f"${p:+.2f}" for p in pnls], textposition="outside",
                textfont=dict(color=COLORS["text_light"], size=11),
            ))
        fig_strat.update_layout(title="PnL par stratégie", xaxis_title="USD",
                                yaxis={"categoryorder": "total ascending"}, height=280)
        apply_dark_theme(fig_strat)

        fig_coins = go.Figure()
        if not fills.empty and "symbol" in fills.columns and "net" in fills.columns:
            cp = fills.groupby("symbol")["net"].sum().sort_values()
            fig_coins.add_trace(go.Bar(
                x=cp.values, y=cp.index, orientation="h",
                marker_color=[COLORS["success"] if v >= 0 else COLORS["danger"] for v in cp.values],
            ))
        fig_coins.update_layout(title="PnL par coin", xaxis_title="USD",
                                yaxis={"categoryorder": "total ascending"}, height=280)
        apply_dark_theme(fig_coins)

        charts = dbc.Row([
            dbc.Col(dcc.Graph(figure=fig_eq,    config={"displayModeBar": False}), width=5),
            dbc.Col(dcc.Graph(figure=fig_strat, config={"displayModeBar": False}), width=4),
            dbc.Col(dcc.Graph(figure=fig_coins, config={"displayModeBar": False}), width=3),
        ])

        return cards, charts
