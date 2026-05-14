"""
orders.py — Orders tab. Reads logs/orders_v9.csv.

Each row of the order log is shown as a coloured DataTable row:
  FILLED      → green
  EXPIRED     → orange
  CANCELLED   → grey
  REJECTED    → red
"""
from __future__ import annotations

import csv
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Input, Output, dash_table, html

from gui.theme import COLORS

_REPO = Path(__file__).resolve().parents[2]
_ORDERS_PATH = _REPO / "logs" / "orders_v9.csv"


def static_layout() -> html.Div:
    return html.Div([
        html.Div(id="orders-table-wrap"),
        html.Div(id="orders-summary", style={"marginTop": "12px"}),
    ])


def _load_orders(max_rows: int = 500) -> list[dict]:
    if not _ORDERS_PATH.exists():
        return []
    rows: list[dict] = []
    try:
        with open(_ORDERS_PATH, encoding="utf-8", errors="replace",
                  newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)
    except Exception:
        return []
    return rows[-max_rows:]


_STATUS_COLOR = {
    "FILL":         COLORS["success"],
    "FILLED":       COLORS["success"],
    "EXPIRE":       COLORS["warning"],
    "EXPIRED":      COLORS["warning"],
    "CANCEL":       COLORS["text"],
    "CANCELLED":    COLORS["text"],
    "REJECT":       COLORS["danger"],
    "REJECTED":     COLORS["danger"],
    "PARTIAL_FILL": COLORS["accent"],
}


def _render_table(rows: list[dict]) -> html.Div:
    if not rows:
        return html.Div("Aucun ordre enregistré (orders_v9.csv vide ou absent).",
                        style={"color": COLORS["text"], "padding": "12px",
                               "fontFamily": "Consolas,monospace",
                               "fontSize": "12px"})

    # Project columns we care about
    cols = ["timestamp", "strategy", "symbol", "side", "order_type",
            "status", "reason",
            "limit_price", "notional_requested", "notional_filled",
            "fill_ratio", "slippage_bps"]
    data = []
    for r in rows:
        row = {c: r.get(c, "") for c in cols}
        data.append(row)

    style_data_conditional = [
        {
            "if": {"filter_query": f'{{status}} = "{status}"'},
            "color": color,
            "fontWeight": "700",
        }
        for status, color in _STATUS_COLOR.items()
    ]

    table = dash_table.DataTable(
        data=data,
        columns=[{"name": c.upper(), "id": c} for c in cols],
        page_size=25,
        style_table={"overflowX": "auto",
                      "backgroundColor": COLORS["card_bg"]},
        style_cell={
            "backgroundColor": COLORS["card_bg"],
            "color": COLORS["text_light"],
            "fontSize": "11px",
            "fontFamily": "Consolas,monospace",
            "padding": "4px 6px",
            "border": f"1px solid {COLORS['grid']}",
            "textAlign": "left",
        },
        style_header={
            "backgroundColor": "#060606",
            "color": COLORS["accent"],
            "fontSize": "10px",
            "fontWeight": "700",
            "letterSpacing": "1px",
        },
        style_data_conditional=style_data_conditional,
        sort_action="native",
        filter_action="native",
    )

    return html.Div(table, style={"backgroundColor": COLORS["card_bg"],
                                   "border": f"1px solid {COLORS['grid']}",
                                   "borderRadius": "4px",
                                   "padding": "8px"})


def _render_summary(rows: list[dict]) -> html.Div:
    n_total = len(rows)
    if n_total == 0:
        return html.Div()
    by_status: dict = {}
    slip_sum, slip_n = 0.0, 0
    for r in rows:
        s = (r.get("status") or "").upper()
        by_status[s] = by_status.get(s, 0) + 1
        if s in ("FILL", "FILLED"):
            try:
                slip_sum += float(r.get("slippage_bps") or 0)
                slip_n   += 1
            except (TypeError, ValueError):
                pass

    parts = [html.Span(f"Total: {n_total}",
                       style={"marginRight": "16px",
                              "color": COLORS["text_light"],
                              "fontWeight": "700"})]
    for s, n in sorted(by_status.items(), key=lambda kv: -kv[1]):
        color = _STATUS_COLOR.get(s, COLORS["text"])
        parts.append(html.Span(f"{s}: {n}",
                               style={"marginRight": "16px",
                                      "color": color,
                                      "fontWeight": "700"}))
    if slip_n > 0:
        parts.append(html.Span(f"Avg slippage: {slip_sum/slip_n:.2f}bps",
                               style={"marginLeft": "8px",
                                      "color": COLORS["accent"],
                                      "fontWeight": "700"}))

    return html.Div(parts, style={
        "padding": "6px 10px",
        "backgroundColor": "#0a0a0a",
        "border": f"1px solid {COLORS['grid']}",
        "borderRadius": "4px",
        "fontFamily": "Consolas,monospace",
        "fontSize": "11px",
        "display": "flex",
        "flexWrap": "wrap",
        "alignItems": "center",
    })


def register_callbacks(app) -> None:

    @app.callback(
        Output("orders-table-wrap", "children"),
        Output("orders-summary",    "children"),
        Input("refresh-interval",   "n_intervals"),
    )
    def _refresh(_n):
        rows = _load_orders()
        return _render_table(rows), _render_summary(rows)
