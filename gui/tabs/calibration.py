"""
calibration.py — Tab 7: live feature values per strategy/coin.
Strategy-specific visualisations:
  - DonchianTrend:           Donchian channel bars per coin
  - RSIBollingerReversion:   RSI + z-score heatmap per coin
  - RotationMomentum:        Momentum score horizontal bars
  - RelativeValue:           Z-score per pair
  - Generic strategies:      DataTable + heatmap (existing)
"""
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, dash_table, dcc, html

from gui.data_loader import load_calibration
from gui.theme import COLORS, STRAT_COLORS, apply_dark_theme, no_data


def static_layout() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col(dcc.Dropdown(id="calib-strat-dd", options=[],
                                 placeholder="Stratégie...",
                                 style={"backgroundColor": COLORS["card_bg"],
                                        "color": "#000"}),
                    width=4),
            dbc.Col(html.Small("Données rafraîchies par l'engine toutes les 60s",
                               style={"color": COLORS["text"], "lineHeight": "2.4"}),
                    width="auto"),
        ], style={"marginBottom": "12px"}),
        html.Div(id="calib-content"),
    ])


def register_callbacks(app) -> None:

    @app.callback(
        Output("calib-content",    "children"),
        Output("calib-strat-dd",   "options"),
        Input("refresh-interval",  "n_intervals"),
        Input("calib-strat-dd",    "value"),
    )
    def update(_n, strat_name):
        calib = load_calibration()
        if not calib or not isinstance(calib, dict):
            return no_data("En attente de runtime/calibration_data.json..."), []

        # Defensive: only keep entries whose value is a {coin: features} dict.
        # Tolerates older / future schemas that may have extra top-level keys.
        calib = {k: v for k, v in calib.items() if isinstance(v, dict)}
        if not calib:
            return no_data("Calibration JSON malformée (aucun dict stratégie)."), []

        options = [{"label": k, "value": k} for k in calib]
        if strat_name not in calib:
            strat_name = next(iter(calib), None)
        if strat_name is None:
            return no_data("Aucune donnée de calibration."), options

        coin_data = calib[strat_name]
        if not isinstance(coin_data, dict) or not coin_data:
            return no_data(f"Pas de données pour {strat_name}."), options
        # Drop any non-dict coin entries to avoid downstream crashes.
        coin_data = {c: v for c, v in coin_data.items() if isinstance(v, dict)}
        if not coin_data:
            return no_data(f"Pas de données pour {strat_name}."), options

        accent = STRAT_COLORS.get(strat_name, COLORS["accent"])

        # ── Strategy-specific charts ─────────────────────────────────
        special = _strategy_specific_chart(strat_name, coin_data, accent)

        # ── Generic feature table (all strategies) ───────────────────
        coins    = list(coin_data.keys())
        features = sorted({k for cd in coin_data.values() for k in cd})

        rows = []
        for coin in coins:
            row = {"Coin": coin}
            for feat in features:
                val = coin_data[coin].get(feat)
                if val is None:
                    row[feat] = "–"
                elif isinstance(val, bool):
                    row[feat] = "⚠" if val else "OK"
                elif isinstance(val, float):
                    row[feat] = f"{val:.4f}"
                else:
                    row[feat] = str(val)
            rows.append(row)

        cols = [{"name": "Coin", "id": "Coin"}] + [{"name": f, "id": f} for f in features]

        table = dash_table.DataTable(
            data=rows, columns=cols,
            style_header={"backgroundColor": "#1a1a1a", "color": accent,
                           "fontWeight": "bold", "fontSize": "11px"},
            style_cell={"backgroundColor": COLORS["card_bg"],
                        "color": COLORS["text"],
                        "border": f"1px solid {COLORS['grid']}",
                        "padding": "4px 8px", "fontSize": "11px"},
            style_table={"overflowX": "auto"},
            style_data_conditional=[
                {"if": {"filter_query": '{has_position} = "True"',
                         "column_id": "has_position"},
                 "backgroundColor": "#003300", "color": COLORS["success"]},
                {"if": {"filter_query": '{wavelet_alert} = "⚠"',
                         "column_id": "wavelet_alert"},
                 "backgroundColor": "#332200", "color": COLORS["warning"]},
                {"if": {"filter_query": '{was_oversold} = "True"',
                         "column_id": "was_oversold"},
                 "backgroundColor": "#001a33", "color": COLORS["accent"]},
            ],
        )

        # Generic numeric heatmap
        numeric_feats, numeric_vals = [], []
        for feat in features:
            col_vals, all_num = [], True
            for coin in coins:
                raw = coin_data[coin].get(feat)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    col_vals.append(float(raw))
                else:
                    all_num = False
                    break
            if all_num and col_vals:
                numeric_feats.append(feat)
                numeric_vals.append(col_vals)

        heatmap_div = html.Div()
        if numeric_feats and coins:
            fig = go.Figure(go.Heatmap(
                z=numeric_vals, x=coins, y=numeric_feats,
                colorscale="RdYlGn", showscale=True,
            ))
            fig.update_layout(title=f"Heatmap features — {strat_name}",
                              height=max(200, len(numeric_feats) * 30 + 100),
                              margin=dict(l=140, r=20, t=40, b=40))
            apply_dark_theme(fig)
            heatmap_div = dcc.Graph(figure=fig, config={"displayModeBar": False})

        return html.Div([
            html.H6(f"Features — {strat_name}",
                    style={"color": accent, "marginBottom": "8px", "fontSize": "12px",
                           "letterSpacing": "1px", "textTransform": "uppercase"}),
            special,
            table,
            html.Div(heatmap_div, style={"marginTop": "16px"}),
        ]), options


# ── Strategy-specific chart builders ──────────────────────────────────────

def _strategy_specific_chart(strat_name: str, coin_data: dict, accent: str) -> html.Div:
    if strat_name == "DonchianTrend":
        return _donchian_chart(coin_data, accent)
    if strat_name == "RSIBollingerReversion":
        return _rsi_bollinger_chart(coin_data, accent)
    if strat_name == "RotationMomentum":
        return _rotation_momentum_chart(coin_data, accent)
    if strat_name == "RelativeValue":
        return _relative_value_chart(coin_data, accent)
    if strat_name == "MeanReversionKalman":
        return _mean_reversion_kalman_chart(coin_data, accent)
    return html.Div()


def _donchian_chart(coin_data: dict, accent: str) -> html.Div:
    """Donchian channel visualisation: upper/mid/lower bars per coin."""
    coins, uppers, mids, lowers, closings = [], [], [], [], []
    for coin, d in coin_data.items():
        if d.get("donchian_upper") and d.get("donchian_lower"):
            coins.append(coin)
            uppers.append(d["donchian_upper"])
            mids.append(d.get("donchian_mid", 0))
            lowers.append(d["donchian_lower"])
            closings.append(d.get("close", d.get("donchian_mid", 0)))
    if not coins:
        return no_data("Pas encore de données Donchian.")

    fig = go.Figure()
    for i, coin in enumerate(coins):
        fig.add_trace(go.Bar(name=coin, x=[coin],
                              y=[uppers[i] - lowers[i]],
                              base=[lowers[i]],
                              marker_color=accent,
                              opacity=0.35, showlegend=False))
        fig.add_trace(go.Scatter(x=[coin], y=[closings[i]],
                                  mode="markers",
                                  marker=dict(color=COLORS["text_light"], size=10,
                                              symbol="diamond"),
                                  name=coin, showlegend=False))
    fig.update_layout(title="Donchian Channel (upper/lower + prix actuel)",
                      barmode="overlay", height=220, yaxis_title="Prix")
    apply_dark_theme(fig)
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style={"marginBottom": "12px"})


def _rsi_bollinger_chart(coin_data: dict, accent: str) -> html.Div:
    """RSI + z-score bar chart per coin."""
    coins, rsi_vals, z_vals = [], [], []
    for coin, d in coin_data.items():
        r = d.get("rsi")
        z = d.get("zscore")
        if r is not None and z is not None:
            coins.append(coin)
            rsi_vals.append(r)
            z_vals.append(z)
    if not coins:
        return no_data("Pas encore de données RSI/Bollinger.")

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name="RSI", x=coins, y=rsi_vals,
        marker_color=[COLORS["danger"] if r < 30 else
                      (COLORS["success"] if r > 70 else COLORS["accent"])
                      for r in rsi_vals],
        yaxis="y",
    ))
    fig.add_hline(y=30,  line_dash="dash", line_color=COLORS["success"], opacity=0.6,
                  annotation_text="30 oversold")
    fig.add_hline(y=70,  line_dash="dash", line_color=COLORS["warning"], opacity=0.6,
                  annotation_text="70 overbought")
    fig.update_layout(title="RSI par coin (ligne 30/70)", height=200)
    apply_dark_theme(fig)
    fig_rsi = dcc.Graph(figure=fig, config={"displayModeBar": False})

    # Z-score bars
    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        name="Z-score", x=coins, y=z_vals,
        marker_color=[COLORS["success"] if z < -2 else
                      (COLORS["warning"] if z > 2 else COLORS["accent"])
                      for z in z_vals],
    ))
    fig2.add_hline(y=-2, line_dash="dash", line_color=COLORS["success"], opacity=0.6)
    fig2.add_hline(y=2,  line_dash="dash", line_color=COLORS["warning"], opacity=0.6)
    fig2.update_layout(title="Z-score par coin (entrée <-2)", height=200)
    apply_dark_theme(fig2)
    fig_z = dcc.Graph(figure=fig2, config={"displayModeBar": False})

    return dbc.Row([
        dbc.Col(fig_rsi, width=6),
        dbc.Col(fig_z,   width=6),
    ], style={"marginBottom": "12px"})


def _rotation_momentum_chart(coin_data: dict, accent: str) -> html.Div:
    """Momentum score horizontal bar chart, sorted descending."""
    pairs = [(coin, d.get("momentum"))
             for coin, d in coin_data.items()
             if d.get("momentum") is not None]
    if not pairs:
        return no_data("Pas encore de scores de momentum.")
    pairs.sort(key=lambda x: x[1])  # ascending for horizontal bar (bottom = worst)
    coins, moms = zip(*pairs)

    fig = go.Figure(go.Bar(
        y=list(coins), x=list(moms), orientation="h",
        marker_color=[COLORS["success"] if m >= 0 else COLORS["danger"] for m in moms],
        text=[f"{m:.4f}" for m in moms],
        textposition="outside",
        textfont=dict(color=COLORS["text_light"], size=10),
    ))
    fig.add_vline(x=0, line_color=COLORS["text"], opacity=0.4)
    fig.update_layout(title="Score de momentum par coin (top = long candidates)",
                      xaxis_title="log return", height=max(220, len(coins) * 24 + 80))
    apply_dark_theme(fig)
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style={"marginBottom": "12px"})


def _relative_value_chart(coin_data: dict, accent: str) -> html.Div:
    """Z-score per pair gauge bars."""
    all_pairs, all_z = [], []
    for coin, d in coin_data.items():
        pairs_data = d.get("pairs", {})
        for pair_key, pd in pairs_data.items():
            z = pd.get("z_score")
            if z is not None:
                all_pairs.append(pair_key)
                all_z.append(z)

    if not all_pairs:
        return no_data("Pas encore de données Relative Value (warmup ≥ 500 bars 1h).")

    fig = go.Figure(go.Bar(
        y=all_pairs, x=all_z, orientation="h",
        marker_color=[COLORS["success"] if z < -2 else
                      (COLORS["warning"] if z < 0 else
                       (COLORS["accent"] if z < 2 else COLORS["danger"]))
                      for z in all_z],
        text=[f"{z:.3f}" for z in all_z],
        textposition="outside",
        textfont=dict(color=COLORS["text_light"], size=10),
    ))
    fig.add_vline(x=-2.0, line_dash="dash", line_color=COLORS["success"],
                  opacity=0.7, annotation_text="entry -2σ")
    fig.add_vline(x=0,    line_color=COLORS["text"], opacity=0.3)
    fig.update_layout(title="Z-score spread relatif par paire",
                      xaxis_title="z-score", height=max(180, len(all_pairs) * 50 + 100))
    apply_dark_theme(fig)
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style={"marginBottom": "12px"})


def _mean_reversion_kalman_chart(coin_data: dict, accent: str) -> html.Div:
    """Z-score gauge bars for MeanReversionKalman."""
    coins, z_vals = [], []
    for coin, d in coin_data.items():
        z = d.get("z_score")
        if z is not None:
            coins.append(coin)
            z_vals.append(z)
    if not coins:
        return html.Div()

    fig = go.Figure(go.Bar(
        y=coins, x=z_vals, orientation="h",
        marker_color=[COLORS["success"] if abs(z) > 1.0 else COLORS["accent"]
                      for z in z_vals],
        text=[f"{z:.3f}" for z in z_vals],
        textposition="outside",
        textfont=dict(color=COLORS["text_light"], size=10),
    ))
    for lvl, c, lbl in [(-1, COLORS["success"], "entry -1σ"),
                         (1,  COLORS["success"], "entry +1σ"),
                         (-4.5, COLORS["danger"], "stop"),
                         (4.5,  COLORS["danger"], "stop")]:
        fig.add_vline(x=lvl, line_dash="dash", line_color=c, opacity=0.5,
                      annotation_text=lbl)
    fig.update_layout(title="Z-score Kalman par coin", height=max(180, len(coins)*35+100))
    apply_dark_theme(fig)
    return dcc.Graph(figure=fig, config={"displayModeBar": False},
                     style={"marginBottom": "12px"})
