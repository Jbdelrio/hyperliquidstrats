"""Dash DataTable styled for the dark theme."""
from __future__ import annotations

from typing import Dict, List

import pandas as pd
from dash import dash_table, html
import dash_bootstrap_components as dbc

from config.endpoints import FLAKY
from config.settings import DARK_THEME
from utils.helpers import format_utc_ms, rate_to_annualized_pct, rate_to_bps


def _to_dataframe(results: List[Dict]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame(columns=["Coin", "Exchange", "Rate", "BPS", "APR %", "Next Funding"])
    rows = []
    for r in results:
        ex_label = r["exchange"].upper() + ("*" if r["exchange"] in FLAKY else "")
        rows.append({
            "Coin": r["coin"],
            "Exchange": ex_label,
            "Rate": round(r["rate"], 6),
            "BPS": round(rate_to_bps(r["rate"]), 2),
            "APR %": round(rate_to_annualized_pct(r["rate"], r["exchange"]), 2),
            "Next Funding": format_utc_ms(r["next_time_ms"]),
        })
    df = pd.DataFrame(rows).sort_values(["Coin", "Exchange"]).reset_index(drop=True)
    return df


def build_rates_table(results: List[Dict]) -> html.Div:
    df = _to_dataframe(results)
    if df.empty:
        return dbc.Alert("No data available — try a different selection.", color="warning")

    return dash_table.DataTable(
        id="rates-table",
        columns=[{"name": c, "id": c} for c in df.columns],
        data=df.to_dict("records"),
        sort_action="native",
        filter_action="native",
        page_action="none",
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": DARK_THEME["background"],
            "color": DARK_THEME["text"],
            "fontWeight": "bold",
            "border": f"1px solid {DARK_THEME['border']}",
        },
        style_cell={
            "backgroundColor": DARK_THEME["card"],
            "color": DARK_THEME["text"],
            "border": f"1px solid {DARK_THEME['border']}",
            "fontFamily": "Inter, 'Fira Code', monospace",
            "padding": "8px 12px",
            "textAlign": "right",
        },
        style_cell_conditional=[
            {"if": {"column_id": "Coin"}, "textAlign": "left", "fontWeight": "bold"},
            {"if": {"column_id": "Exchange"}, "textAlign": "left"},
            {"if": {"column_id": "Next Funding"}, "textAlign": "center"},
        ],
        style_data_conditional=[
            {
                "if": {"filter_query": "{Rate} > 0", "column_id": "Rate"},
                "color": DARK_THEME["success"],
            },
            {
                "if": {"filter_query": "{Rate} < 0", "column_id": "Rate"},
                "color": DARK_THEME["danger"],
            },
            {
                "if": {"filter_query": "{BPS} > 0", "column_id": "BPS"},
                "color": DARK_THEME["success"],
            },
            {
                "if": {"filter_query": "{BPS} < 0", "column_id": "BPS"},
                "color": DARK_THEME["danger"],
            },
            {
                "if": {"filter_query": "{APR %} > 0", "column_id": "APR %"},
                "color": DARK_THEME["success"],
            },
            {
                "if": {"filter_query": "{APR %} < 0", "column_id": "APR %"},
                "color": DARK_THEME["danger"],
            },
        ],
    )
