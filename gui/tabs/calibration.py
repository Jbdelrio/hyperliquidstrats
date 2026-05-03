"""
calibration.py — Tab 7: live feature values per strategy/coin.
Reads runtime/calibration_data.json (written by engine every 60s).
"""
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, dash_table, dcc, html

from gui.data_loader import load_calibration
from gui.theme import COLORS, apply_dark_theme, no_data


def static_layout() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col(
                dcc.Dropdown(
                    id="calib-strat-dd",
                    options=[],
                    placeholder="Stratégie...",
                    style={"backgroundColor": COLORS["card_bg"], "color": "#000"},
                ),
                width=3,
            ),
            dbc.Col(
                html.Small("Données rafraîchies par l'engine toutes les 60s",
                           style={"color": COLORS["text"],
                                  "lineHeight": "2.4"}),
                width="auto",
            ),
        ], style={"marginBottom": "12px"}),
        html.Div(id="calib-content"),
    ])


def register_callbacks(app) -> None:

    @app.callback(
        Output("calib-content", "children"),
        Output("calib-strat-dd", "options"),
        Input("refresh-interval", "n_intervals"),
        Input("calib-strat-dd", "value"),
    )
    def update(n, strat_name):
        calib = load_calibration()
        if not calib:
            return no_data("En attente de runtime/calibration_data.json..."), []

        options = [{"label": k, "value": k} for k in calib]
        if strat_name not in calib:
            strat_name = next(iter(calib), None)
        if strat_name is None:
            return no_data("Aucune donnée de calibration."), options

        coin_data = calib[strat_name]  # dict: {coin: {feature: value}}
        if not coin_data:
            return no_data(f"Pas de données pour {strat_name}."), options

        # ── Feature table ────────────────────────────────────────────
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

        cols = [{"name": "Coin", "id": "Coin"}] + \
               [{"name": f, "id": f} for f in features]

        table = dash_table.DataTable(
            data=rows,
            columns=cols,
            style_header={"backgroundColor": "#1a1a1a",
                           "color": COLORS["accent"],
                           "fontWeight": "bold", "fontSize": "12px"},
            style_cell={"backgroundColor": COLORS["card_bg"],
                        "color": COLORS["text"],
                        "border": f"1px solid {COLORS['grid']}",
                        "padding": "5px 8px",
                        "fontSize": "11px"},
            style_table={"overflowX": "auto"},
            style_data_conditional=[
                {
                    "if": {"filter_query": '{has_position} = "True"',
                           "column_id": "has_position"},
                    "backgroundColor": "#003300",
                    "color": COLORS["success"],
                },
                {
                    "if": {"filter_query": '{wavelet_alert} = "⚠"',
                           "column_id": "wavelet_alert"},
                    "backgroundColor": "#332200",
                    "color": COLORS["warning"],
                },
            ],
        )

        # ── Heatmap of numeric features ──────────────────────────────
        numeric_feats = []
        numeric_vals  = []
        for feat in features:
            col_vals = []
            all_numeric = True
            for coin in coins:
                raw = coin_data[coin].get(feat)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    col_vals.append(float(raw))
                else:
                    all_numeric = False
                    break
            if all_numeric and col_vals:
                numeric_feats.append(feat)
                numeric_vals.append(col_vals)

        heatmap_div = html.Div()
        if numeric_feats and coins:
            fig = go.Figure(go.Heatmap(
                z=numeric_vals,
                x=coins,
                y=numeric_feats,
                colorscale="RdYlGn",
                showscale=True,
            ))
            fig.update_layout(
                title=f"Heatmap features — {strat_name}",
                height=max(200, len(numeric_feats) * 30 + 100),
                margin=dict(l=120, r=20, t=40, b=40),
            )
            apply_dark_theme(fig)
            heatmap_div = dcc.Graph(figure=fig,
                                    config={"displayModeBar": False})

        return html.Div([
            html.H6(f"Features — {strat_name}",
                    style={"color": COLORS["text_light"],
                           "marginBottom": "8px"}),
            table,
            html.Div(heatmap_div, style={"marginTop": "16px"}),
        ]), options
