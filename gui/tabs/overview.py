"""
overview.py — Tab 1: global KPIs + per-strategy cards + equity curve.
IDs overview-cards / overview-charts preserved for compatibility.
"""
import time

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from gui.data_loader import load_fills, load_metrics, load_strategy_status
from gui.theme import COLORS, STRAT_COLORS, apply_dark_theme, no_data, stat_card

_ALL_STRATS = [
    "S8EMS", "MomentumLS", "BreakoutControlled",
    "MeanReversionKalman", "FundingArbitrage",
    "DonchianTrend", "RSIBollingerReversion",
    "RotationMomentum", "RelativeValue",
]
_DFLT_CAP = {
    "S8EMS": 500, "MomentumLS": 500, "BreakoutControlled": 500,
    "MeanReversionKalman": 500, "FundingArbitrage": 500,
    "DonchianTrend": 500, "RSIBollingerReversion": 500,
    "RotationMomentum": 0, "RelativeValue": 500,
}
# Total capital = sum of all strategy allocations (computed dynamically)
_DFLT_TOTAL_CAP = float(sum(_DFLT_CAP.values()))  # 4000.0


def _total_capital(live_list: list) -> float:
    """Sum of capital_allocated_usd from live engine data, or fall back to defaults."""
    if live_list:
        total = sum(float(s.get("capital_allocated_usd", 0) or 0) for s in live_list)
        if total > 0:
            return total
    return _DFLT_TOTAL_CAP

_BDR = f"1px solid {COLORS['grid']}"
_TD  = {"border": _BDR, "padding": "4px 8px", "fontSize": "11px",
        "fontFamily": "Consolas,monospace", "color": COLORS["text_light"]}
_TH2 = {"backgroundColor": "#060606", "color": COLORS["accent"],
        "fontWeight": "bold", "fontSize": "10px",
        "border": _BDR, "padding": "4px 8px", "letterSpacing": "1px"}


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="overview-cards"),
        html.Div(id="overview-charts", style={"marginTop": "16px"}),
    ])


def _wr_bar(wr: float, color: str):
    return html.Div(
        html.Div(style={
            "width": f"{min(max(wr, 0), 100):.1f}%",
            "height": "4px",
            "backgroundColor": color,
            "borderRadius": "2px",
        }),
        style={"height": "4px", "marginTop": "4px",
               "backgroundColor": "#222", "borderRadius": "2px"},
    )


def _strat_card(name, status, cap, cum_pnl, n_trades, wr, n_pos):
    sc     = {"ACTIF": COLORS["success"], "INACTIF": COLORS["danger"],
              "SUSPENDU": COLORS["warning"]}.get(status, COLORS["text"])
    accent = STRAT_COLORS.get(name, COLORS["accent"])
    pnl_txt   = f"${cum_pnl:+.2f}" if cum_pnl is not None else "N/A"
    pnl_color = (COLORS["success"] if (cum_pnl or 0) >= 0
                 else COLORS["danger"]) if cum_pnl is not None else COLORS["text"]
    wr_txt    = f"{wr:.0f}%" if wr is not None else "—"
    wr_color  = COLORS["success"] if (wr or 0) >= 50 else COLORS["warning"]

    return dbc.Card(dbc.CardBody([
        dbc.Row([
            dbc.Col(html.B(name, style={"color": accent, "fontSize": "11px"}),
                    width="auto"),
            dbc.Col(dbc.Badge(status, style={"backgroundColor": sc, "fontSize": "8px",
                                              "padding": "2px 5px"}), width="auto"),
            dbc.Col(width=True),
            dbc.Col(html.Span(f"${cap:.0f}",
                              style={"color": COLORS["text"], "fontSize": "10px",
                                     "fontFamily": "Consolas,monospace"}), width="auto"),
        ], className="g-1 align-items-center mb-1"),
        dbc.Row([
            dbc.Col([html.Div("PnL", style={"color": COLORS["text"], "fontSize": "8px"}),
                     html.Div(pnl_txt, style={"color": pnl_color, "fontWeight": "700",
                                               "fontSize": "13px",
                                               "fontFamily": "Consolas,monospace"})], width=4),
            dbc.Col([html.Div("Trades", style={"color": COLORS["text"], "fontSize": "8px"}),
                     html.Div(str(n_trades), style={"color": COLORS["text_light"],
                                                     "fontWeight": "700", "fontSize": "12px",
                                                     "fontFamily": "Consolas,monospace"})], width=3),
            dbc.Col([html.Div("WR", style={"color": COLORS["text"], "fontSize": "8px"}),
                     html.Div(wr_txt, style={"color": wr_color, "fontWeight": "700",
                                              "fontSize": "12px",
                                              "fontFamily": "Consolas,monospace"})], width=3),
            dbc.Col([html.Div("Pos", style={"color": COLORS["text"], "fontSize": "8px"}),
                     html.Div(str(n_pos),
                              style={"color": COLORS["warning"] if n_pos else COLORS["text"],
                                      "fontWeight": "700", "fontSize": "13px",
                                      "fontFamily": "Consolas,monospace"})], width=2),
        ]),
        _wr_bar(wr or 0, wr_color),
    ], style={"padding": "8px 10px"}),
    className="card-glow",
    style={"backgroundColor": COLORS["card_bg"], "border": _BDR,
           "borderRadius": "4px", "borderLeft": f"3px solid {accent}"})


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
            html.Td(p.get("symbol",   "?"), style={**_TD, "color": COLORS["accent"],
                                                    "fontWeight": "700"}),
            html.Td(p.get("side",     "?"), style={**_TD, "color": sc, "fontWeight": "700"}),
            html.Td(f"${p.get('notional_usd', 0):.0f}", style=_TD),
            html.Td(f"{p.get('entry_price', 0):.5g}",   style=_TD),
            html.Td(f"{p.get('current_price', 0):.5g}", style=_TD),
            html.Td(f"${upnl:+.4f}", style={**_TD, "color": uc, "fontWeight": "700"}),
            html.Td(f"{p.get('hold_s', 0)}s", style=_TD),
        ]))
    thead = html.Thead(html.Tr(
        [html.Th(h, style=_TH2)
         for h in ["STRAT", "COIN", "SIDE", "NOTIO", "ENTRY", "MID", "UPNL", "HOLD"]]
    ))
    return html.Div([
        html.P("POSITIONS OUVERTES",
               style={"color": COLORS["warning"], "letterSpacing": "2px",
                       "fontSize": "10px", "marginBottom": "4px", "fontWeight": "700"}),
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

        # Total capital = sum of all strategy allocations (dynamic)
        init_cap = _total_capital(live_list)

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

        if not metrics.empty:
            last    = metrics.iloc[-1]
            equity  = float(last.get("equity",   init_cap) or init_cap)
            pnl_day = float(last.get("pnl_day",  0) or 0)
            pnl_1h  = float(last.get("pnl_hour", 0) or 0)
            wins    = int(last.get("wins",   0) or 0)
            losses  = int(last.get("losses", 0) or 0)
            total   = wins + losses
            wr_txt  = f"{100*wins/total:.1f}%" if total else "—"
            dd      = (equity - init_cap) / init_cap * 100 if init_cap else 0.0
        else:
            equity = init_cap; pnl_day = pnl_1h = 0.0
            total = wins = 0; wr_txt = "—"; dd = 0.0

        all_pos = []
        for s in live_list:
            for p in s.get("open_positions", []):
                p["strategy"] = s.get("name", "?")
                all_pos.append(p)

        # ── Global KPIs ───────────────────────────────────────────────
        g_cards = dbc.Row([
            dbc.Col(stat_card("Capital total", f"${init_cap:.0f}", COLORS["text_light"])),
            dbc.Col(stat_card("Equity",    f"${equity:.2f}", COLORS["accent"])),
            dbc.Col(stat_card("PnL today", f"${pnl_day:+.2f}",
                              COLORS["success"] if pnl_day >= 0 else COLORS["danger"])),
            dbc.Col(stat_card("PnL 1h",   f"${pnl_1h:+.2f}",
                              COLORS["success"] if pnl_1h >= 0 else COLORS["danger"])),
            dbc.Col(stat_card("Drawdown",  f"{dd:+.2f}%",
                              COLORS["danger"] if dd < -1 else COLORS["success"])),
            dbc.Col(stat_card("Trades",    str(total), COLORS["text_light"])),
            dbc.Col(stat_card("Win rate",  wr_txt, COLORS["accent"])),
            dbc.Col(stat_card("Positions", str(len(all_pos)),
                              COLORS["warning"] if all_pos else COLORS["text"])),
        ], className="g-2")

        # ── Strategy rows ─────────────────────────────────────────────
        def _make_row(names):
            cols = []
            for name in names:
                live  = live_map.get(name, {})
                cap   = float(live.get("capital_allocated_usd",
                                       _DFLT_CAP.get(name, 500)) or _DFLT_CAP.get(name, 500))
                ena   = live.get("enabled", False) if live else False
                susp  = float(live.get("suspended_until", 0) or 0)
                status = "SUSPENDU" if susp > now else ("ACTIF" if ena else "INACTIF")
                n_pos  = len(live.get("open_positions", [])) if live else 0
                st     = strat_stats.get(name, {})
                cols.append(dbc.Col(_strat_card(name, status, cap,
                                               st.get("pnl"), st.get("n", 0),
                                               st.get("wr"), n_pos), width=True))
            return dbc.Row(cols, className="g-2", style={"marginTop": "8px"})

        def _grp(txt):
            return html.P(txt, style={"color": COLORS["text"], "fontSize": "9px",
                                       "letterSpacing": "2px", "textTransform": "uppercase",
                                       "marginTop": "12px", "marginBottom": "0"})

        existing  = ["S8EMS", "MomentumLS", "BreakoutControlled",
                     "MeanReversionKalman", "FundingArbitrage"]
        nouvelles = ["DonchianTrend", "RSIBollingerReversion",
                     "RotationMomentum", "RelativeValue"]

        cards = html.Div([
            g_cards,
            _grp("Stratégies existantes"),
            _make_row(existing),
            _grp("Nouvelles stratégies"),
            _make_row(nouvelles),
            _pos_table(all_pos),
        ])

        # ── Charts ────────────────────────────────────────────────────
        fig_eq = go.Figure()
        if not metrics.empty and "dt" in metrics.columns and "equity" in metrics.columns:
            eq_vals = metrics["equity"].tolist()
            lc = COLORS["success"] if (eq_vals[-1] if eq_vals else 0) >= init_cap else COLORS["danger"]
            r = int(lc[1:3], 16); g_ = int(lc[3:5], 16); b_ = int(lc[5:7], 16)
            fill_rgba = f"rgba({r},{g_},{b_},0.08)"
            fig_eq.add_trace(go.Scatter(
                x=metrics["dt"], y=metrics["equity"],
                mode="lines", name="Equity",
                line=dict(color=lc, width=2),
                fill="tozeroy", fillcolor=fill_rgba,
            ))
            fig_eq.add_hline(y=init_cap, line_dash="dot",
                             line_color=COLORS["warning"], opacity=0.4,
                             annotation_text=f"Capital initial ${init_cap:.0f}")
        fig_eq.update_layout(title="Equity curve", showlegend=False, height=260)
        apply_dark_theme(fig_eq)

        fig_strat = go.Figure()
        active_strats = [n for n in _ALL_STRATS if n in strat_stats]
        if active_strats:
            pnls   = [strat_stats[n]["pnl"] for n in active_strats]
            colors = [STRAT_COLORS.get(n, COLORS["accent"]) for n in active_strats]
            fig_strat.add_trace(go.Bar(
                x=pnls, y=active_strats, orientation="h",
                marker_color=colors,
                text=[f"${p:+.2f}" for p in pnls],
                textposition="outside",
                textfont=dict(color=COLORS["text_light"], size=10),
            ))
        fig_strat.update_layout(title="PnL par stratégie", xaxis_title="USD",
                                yaxis={"categoryorder": "total ascending"}, height=260)
        apply_dark_theme(fig_strat)

        fig_coins = go.Figure()
        if not fills.empty and "symbol" in fills.columns and "net" in fills.columns:
            cp = fills.groupby("symbol")["net"].sum().sort_values()
            fig_coins.add_trace(go.Bar(
                x=cp.values, y=cp.index, orientation="h",
                marker_color=[COLORS["success"] if v >= 0 else COLORS["danger"]
                              for v in cp.values],
            ))
        fig_coins.update_layout(title="PnL par coin", xaxis_title="USD",
                                yaxis={"categoryorder": "total ascending"}, height=260)
        apply_dark_theme(fig_coins)

        charts = dbc.Row([
            dbc.Col(dcc.Graph(figure=fig_eq,    config={"displayModeBar": False}), width=5),
            dbc.Col(dcc.Graph(figure=fig_strat, config={"displayModeBar": False}), width=4),
            dbc.Col(dcc.Graph(figure=fig_coins, config={"displayModeBar": False}), width=3),
        ])

        return cards, charts
