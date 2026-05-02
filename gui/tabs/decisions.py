"""
decisions.py — Tab 2: PLACE/SKIP breakdown, top skip reasons, per-coin table,
               what-if min_spread_bps slider.
"""
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from dash import Input, Output, State, dash_table, dcc, html

from gui.data_loader import recent_decisions
from gui.theme import COLORS, apply_dark_theme, no_data


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="decisions-top"),
        html.Hr(style={"borderColor": COLORS["grid"]}),
        dbc.Row([
            dbc.Col([
                html.Label("What-if: min_spread_bps",
                           style={"color": COLORS["text"], "marginBottom": "4px"}),
                dcc.Slider(
                    id="whatif-slider",
                    min=0.5, max=5.0, step=0.5, value=2.0,
                    marks={v / 2: f"{v / 2:.1f}" for v in range(1, 11)},
                    tooltip={"placement": "bottom", "always_visible": True},
                ),
                html.Div(id="whatif-output", style={"marginTop": "12px"}),
            ]),
        ], style={"marginTop": "12px"}),
    ])


def register_callbacks(app) -> None:
    @app.callback(
        Output("decisions-top", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def update_top(n):
        df = recent_decisions(hours=2.0)
        if df.empty:
            return no_data()

        total = len(df)
        by_type = df["decision"].value_counts()
        place_n = int(by_type.get("PLACE", 0))
        skip_n  = int(by_type.get("SKIP",  0))

        # Pie: PLACE / SKIP
        fig_pie = px.pie(
            values=[place_n, skip_n],
            names=["PLACE", "SKIP"],
            color_discrete_sequence=[COLORS["success"], COLORS["danger"]],
            title=f"Répartition decisions — {total} au total (2h)",
            hole=0.4,
        )
        apply_dark_theme(fig_pie)

        # Bar: top skip reasons
        skips = df[df["decision"] == "SKIP"]
        if not skips.empty:
            top_reasons = (
                skips["reason"].value_counts()
                .head(10)
                .reset_index()
            )
            top_reasons.columns = ["reason", "count"]
            fig_bar = px.bar(
                top_reasons, x="count", y="reason", orientation="h",
                title="Top 10 raisons de skip",
                color_discrete_sequence=[COLORS["warning"]],
            )
            fig_bar.update_layout(yaxis={"categoryorder": "total ascending"})
            apply_dark_theme(fig_bar)
        else:
            fig_bar = go.Figure()
            apply_dark_theme(fig_bar)

        # Per-coin table
        coins = df["symbol"].unique()
        rows = []
        for coin in sorted(coins):
            cdf = df[df["symbol"] == coin]
            n = len(cdf)
            p = int((cdf["decision"] == "PLACE").sum())
            s = int((cdf["decision"] == "SKIP").sum())
            pp = 100 * p / n if n > 0 else 0.0
            sp = 100 * s / n if n > 0 else 0.0
            top_skip = (
                cdf[cdf["decision"] == "SKIP"]["reason"].mode().iloc[0]
                if s > 0 else "—"
            )
            rows.append({
                "Coin":          coin,
                "Total":         n,
                "PLACE %":       f"{pp:.1f}%",
                "SKIP %":        f"{sp:.1f}%",
                "Top skip reason": top_skip,
                "_place_rate":   pp,
            })

        table_df = pd.DataFrame(rows)

        style_data_cond = []
        for i, row in table_df.iterrows():
            rate = row["_place_rate"]
            if rate < 5:
                bg = "#3a0000"
            elif rate > 30:
                bg = "#003a00"
            else:
                bg = COLORS["card_bg"]
            style_data_cond.append({
                "if": {"row_index": i},
                "backgroundColor": bg,
            })

        table = dash_table.DataTable(
            data=table_df.drop(columns=["_place_rate"]).to_dict("records"),
            columns=[{"name": c, "id": c} for c in table_df.columns if c != "_place_rate"],
            style_table={"overflowX": "auto"},
            style_header={"backgroundColor": "#1a1a1a", "color": COLORS["accent"],
                          "fontWeight": "bold"},
            style_cell={"backgroundColor": COLORS["card_bg"], "color": COLORS["text"],
                        "border": f"1px solid {COLORS['grid']}", "textAlign": "left",
                        "padding": "6px"},
            style_data_conditional=style_data_cond,
        )

        return html.Div([
            dbc.Row([
                dbc.Col(dcc.Graph(figure=fig_pie,
                                  config={"displayModeBar": False}), width=5),
                dbc.Col(dcc.Graph(figure=fig_bar,
                                  config={"displayModeBar": False}), width=7),
            ]),
            dbc.Row([dbc.Col([html.H6("Par coin", style={"color": COLORS["accent"],
                                                          "marginTop": "12px"}),
                               table])]),
        ])

    @app.callback(
        Output("whatif-output", "children"),
        Input("whatif-slider",  "value"),
        Input("refresh-interval", "n_intervals"),
    )
    def update_whatif(spread_val, n):
        df = recent_decisions(hours=2.0)
        if df.empty:
            return no_data()

        skips = df[df["decision"] == "SKIP"]
        total_skips = len(skips)
        spread_skips = skips[
            (skips["reason"] == "spread_too_tight") &
            (skips["spread_bps"].notna()) &
            (skips["spread_bps"] >= spread_val)
        ]
        recovered = len(spread_skips)
        pct = 100.0 * recovered / total_skips if total_skips > 0 else 0.0

        avg_spread = (
            spread_skips["spread_bps"].mean()
            if recovered > 0 else None
        )
        avg_txt = f" — spread moyen récupéré : {avg_spread:.2f} bps" if avg_spread else ""

        color = COLORS["success"] if recovered > 0 else COLORS["text"]
        return html.Div([
            html.Span(
                f"Avec min_spread_bps = {spread_val:.1f} bps → "
                f"{recovered} / {total_skips} skips récupérés "
                f"({pct:.1f}%){avg_txt}",
                style={"color": color},
            ),
        ])
