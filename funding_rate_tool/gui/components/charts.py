"""Plotly charts for arbitrage view + funding-rate heatmap."""
from __future__ import annotations

from typing import Dict, List

import pandas as pd
import plotly.graph_objects as go
from dash import dash_table, dcc, html
import dash_bootstrap_components as dbc

from config.endpoints import FLAKY
from config.settings import DARK_THEME
from utils.helpers import calculate_arbitrage, format_utc_ms, rate_to_annualized_pct, rate_to_bps


def _layout(title: str, height: int = 380) -> dict:
    return {
        "title": {"text": title, "x": 0.5, "font": {"color": DARK_THEME["text"]}},
        "plot_bgcolor": DARK_THEME["card"],
        "paper_bgcolor": DARK_THEME["background"],
        "font": {"color": DARK_THEME["text"], "family": "Inter, sans-serif"},
        "height": height,
        "margin": {"l": 50, "r": 30, "t": 50, "b": 50},
        "xaxis": {"gridcolor": DARK_THEME["border"], "zerolinecolor": DARK_THEME["border"]},
        "yaxis": {"gridcolor": DARK_THEME["border"], "zerolinecolor": DARK_THEME["border"]},
    }


def build_arbitrage_view(results: List[Dict], alert_bps: float = 0.0) -> html.Div:
    opps = calculate_arbitrage(results)
    if not opps:
        return dbc.Alert("No arbitrage opportunities (need ≥2 exchanges per coin).", color="warning")

    coins = [o["coin"] for o in opps]
    spreads = [o["spread_bps"] for o in opps]
    colors = [
        DARK_THEME["success"] if s >= alert_bps else DARK_THEME["primary"]
        for s in spreads
    ]
    text_labels = [
        f"{o['long_exchange'].upper()} → {o['short_exchange'].upper()}"
        for o in opps
    ]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=coins,
        y=spreads,
        marker_color=colors,
        text=[f"{s:.2f} bps" for s in spreads],
        textposition="outside",
        hovertext=text_labels,
        hovertemplate="<b>%{x}</b><br>Spread: %{y:.2f} bps<br>%{hovertext}<extra></extra>",
    ))
    if alert_bps > 0:
        fig.add_hline(
            y=alert_bps,
            line=dict(color=DARK_THEME["warning"], dash="dash"),
            annotation_text=f"alert @ {alert_bps} bps",
            annotation_font_color=DARK_THEME["warning"],
        )
    fig.update_layout(**_layout("Arbitrage spreads (bps)"))

    df = pd.DataFrame([{
        "Coin": o["coin"],
        "Long": o["long_exchange"].upper() + ("*" if o["long_exchange"] in FLAKY else ""),
        "Short": o["short_exchange"].upper() + ("*" if o["short_exchange"] in FLAKY else ""),
        "Spread bps": round(o["spread_bps"], 2),
        "Long rate": round(o["long_rate"], 6),
        "Short rate": round(o["short_rate"], 6),
        "Next funding": format_utc_ms(o["next_time_ms"]),
        "Alert": "HIT" if o["spread_bps"] >= alert_bps else "",
    } for o in opps])

    table = dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in df.columns],
        data=df.to_dict("records"),
        sort_action="native",
        style_header={
            "backgroundColor": DARK_THEME["background"],
            "color": DARK_THEME["text"],
            "fontWeight": "bold",
        },
        style_cell={
            "backgroundColor": DARK_THEME["card"],
            "color": DARK_THEME["text"],
            "border": f"1px solid {DARK_THEME['border']}",
            "padding": "6px 10px",
        },
        style_data_conditional=[
            {"if": {"filter_query": "{Alert} = 'HIT'"},
             "backgroundColor": "rgba(210, 153, 34, 0.2)"},
        ],
    )
    return html.Div([dcc.Graph(figure=fig), html.Div(table, className="mt-3")])


def build_top_n_view(rows: List[Dict], exchange: str, mode: str) -> html.Div:
    if not rows:
        return dbc.Alert(f"No data for {exchange}. (Top-N supported: binance, bitget, "
                         "gateio, hyperliquid.)", color="warning")

    mode_label = {"abs": "Most extreme |rate|",
                  "high": "Highest (most positive)",
                  "low": "Lowest (most negative)"}.get(mode, mode)
    title = f"Top {len(rows)} on {exchange.upper()} — {mode_label}"

    symbols = [r.get("symbol") or r["coin"] for r in rows]
    bps = [rate_to_bps(r["rate"]) for r in rows]
    colors = [DARK_THEME["success"] if v >= 0 else DARK_THEME["danger"] for v in bps]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=symbols,
        y=bps,
        marker_color=colors,
        text=[f"{v:+.2f}" for v in bps],
        textposition="outside",
        hovertemplate="<b>%{x}</b><br>%{y:+.2f} bps<extra></extra>",
    ))
    fig.update_layout(**_layout(title))
    fig.update_xaxes(tickangle=-45)

    df = pd.DataFrame([{
        "#": i + 1,
        "Symbol": r.get("symbol") or r["coin"],
        "Rate": round(r["rate"], 6),
        "BPS": round(rate_to_bps(r["rate"]), 2),
        "APR %": round(rate_to_annualized_pct(r["rate"], r["exchange"]), 2),
        "Next funding": format_utc_ms(r["next_time_ms"]),
    } for i, r in enumerate(rows)])

    table = dash_table.DataTable(
        columns=[{"name": c, "id": c} for c in df.columns],
        data=df.to_dict("records"),
        sort_action="native",
        style_header={
            "backgroundColor": DARK_THEME["background"],
            "color": DARK_THEME["text"],
            "fontWeight": "bold",
        },
        style_cell={
            "backgroundColor": DARK_THEME["card"],
            "color": DARK_THEME["text"],
            "border": f"1px solid {DARK_THEME['border']}",
            "padding": "6px 10px",
            "textAlign": "right",
        },
        style_cell_conditional=[
            {"if": {"column_id": "Symbol"}, "textAlign": "left"},
            {"if": {"column_id": "Next funding"}, "textAlign": "center"},
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{BPS} > 0", "column_id": "BPS"},
             "color": DARK_THEME["success"]},
            {"if": {"filter_query": "{BPS} < 0", "column_id": "BPS"},
             "color": DARK_THEME["danger"]},
            {"if": {"filter_query": "{APR %} > 0", "column_id": "APR %"},
             "color": DARK_THEME["success"]},
            {"if": {"filter_query": "{APR %} < 0", "column_id": "APR %"},
             "color": DARK_THEME["danger"]},
        ],
    )
    return html.Div([dcc.Graph(figure=fig), html.Div(table, className="mt-3")])


def build_heatmap(results: List[Dict]) -> html.Div:
    if not results:
        return dbc.Alert("Need data to render heatmap.", color="warning")
    df = pd.DataFrame([{
        "coin": r["coin"],
        "exchange": r["exchange"].upper(),
        "bps": r["rate"] * 10_000,
    } for r in results])
    pivot = df.pivot_table(index="exchange", columns="coin", values="bps", aggfunc="mean")

    fig = go.Figure(data=go.Heatmap(
        z=pivot.values,
        x=list(pivot.columns),
        y=list(pivot.index),
        colorscale=[
            [0.0, DARK_THEME["danger"]],
            [0.5, DARK_THEME["card"]],
            [1.0, DARK_THEME["success"]],
        ],
        zmid=0,
        text=[[f"{v:+.2f}" if pd.notna(v) else "" for v in row] for row in pivot.values],
        texttemplate="%{text}",
        textfont={"color": DARK_THEME["text"]},
        hovertemplate="%{y} / %{x}: %{z:+.2f} bps<extra></extra>",
    ))
    fig.update_layout(**_layout("Funding-rate heatmap (bps)", height=320))
    return dcc.Graph(figure=fig)
