"""
gui/tabs/llm_overlay.py — Dashboard tab for the LLM overlay.

Shows: enabled/arch, last decision per symbol, prob_up/down, allow_trade,
risk flags, rolling Brier score, prediction log table.
Safe when LLM is disabled (all fields show "disabled").
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Input, Output, dash_table, dcc, html

from gui.theme import COLORS

_REPO = Path(__file__).parent.parent.parent
_LLM_CSV = _REPO / "data" / "llm_predictions.csv"


def static_layout() -> list:
    return [
        html.H5("LLM Overlay", style={"color": COLORS["accent"], "marginBottom": "12px"}),
        dbc.Row([
            dbc.Col(_status_card(), width=4),
            dbc.Col(_last_decision_card(), width=8),
        ], className="mb-3"),
        dbc.Row([
            dbc.Col(_brier_card(), width=4),
            dbc.Col(_predictions_table(), width=8),
        ]),
        dcc.Store(id="llm-data-store"),
    ]


def _status_card():
    return dbc.Card([
        dbc.CardHeader("Status", style={"color": COLORS["accent"]}),
        dbc.CardBody(html.Div(id="llm-status-body")),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"})


def _last_decision_card():
    return dbc.Card([
        dbc.CardHeader("Last Decisions by Symbol", style={"color": COLORS["accent"]}),
        dbc.CardBody(html.Div(id="llm-decisions-body")),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"})


def _brier_card():
    return dbc.Card([
        dbc.CardHeader("Rolling Brier Score (last 50)", style={"color": COLORS["accent"]}),
        dbc.CardBody(html.Div(id="llm-brier-body")),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"})


def _predictions_table():
    return dbc.Card([
        dbc.CardHeader("Recent Predictions", style={"color": COLORS["accent"]}),
        dbc.CardBody(html.Div(id="llm-table-body")),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"})


def _enabled() -> bool:
    return os.environ.get("LLM_ENABLED", "false").lower() in ("1", "true", "yes")


def register_callbacks(app) -> None:

    @app.callback(
        Output("llm-status-body", "children"),
        Output("llm-decisions-body", "children"),
        Output("llm-brier-body", "children"),
        Output("llm-table-body", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def _update(_n):
        # Read runtime LLM status written by engine (reflects actual live state)
        llm_rt = _REPO / "runtime" / "llm_status.json"
        rt_data: dict = {}
        try:
            if llm_rt.exists():
                import time as _t
                if _t.time() - llm_rt.stat().st_mtime < 120:
                    with open(llm_rt, encoding="utf-8") as _f:
                        rt_data = json.load(_f)
        except Exception:
            pass

        enabled = rt_data.get("enabled", _enabled())
        arch    = rt_data.get("architecture",
                              os.environ.get("LLM_ARCHITECTURE", "independent_ensemble"))
        sr      = rt_data.get("sample_rate", 1.0)
        model   = os.environ.get("LLM_MODEL", "—")

        # ── Status ──
        status_items = [
            _kv("Enabled",      "YES" if enabled else "NO",
                COLORS["success"] if enabled else COLORS["danger"]),
            _kv("Architecture", arch),
            _kv("Sample rate",  f"{sr*100:.0f}% des appels"),
            _kv("Model",        model),
            _kv("Provider",     os.environ.get("LLM_PROVIDER", "—")),
        ]
        status_out = html.Div(status_items)

        # ── Last decisions ──
        decisions_out = _build_decisions_panel()

        # ── Brier ──
        brier_val = _get_brier()
        brier_color = (
            COLORS["success"]  if brier_val is not None and brier_val < 0.2 else
            COLORS["warning"]  if brier_val is not None and brier_val < 0.25 else
            COLORS["danger"]
        )
        brier_out = html.H3(
            f"{brier_val:.4f}" if brier_val is not None else "—",
            style={"color": brier_color, "textAlign": "center"},
        )

        # ── Table ──
        table_out = _build_predictions_table()

        return status_out, decisions_out, brier_out, table_out


def _kv(label: str, value: str, color: str = None) -> html.Div:
    return html.Div([
        html.Span(f"{label}: ", style={"color": COLORS["text"], "fontWeight": "600"}),
        html.Span(value, style={"color": color or COLORS["text_light"]}),
    ], style={"marginBottom": "4px"})


def _build_decisions_panel() -> html.Div:
    """Read last decisions from runtime/llm_status.json if available."""
    llm_status = _REPO / "runtime" / "llm_status.json"
    if not llm_status.exists():
        return html.Small("No LLM decisions logged yet.", style={"color": COLORS["text"]})

    try:
        with open(llm_status) as f:
            data = json.load(f)
    except Exception:
        return html.Small("Error reading LLM status.", style={"color": COLORS["danger"]})

    rows = []
    for sym, dec in data.get("last_decisions", {}).items():
        if not isinstance(dec, dict):
            continue
        action    = dec.get("final_action", "?")
        allow     = dec.get("allow_trade", False)
        prob_up   = dec.get("final_prob_up", 0.5)
        conf      = dec.get("final_confidence", "?")
        flags     = ", ".join(dec.get("risk_flags", []))[:60]
        color_map = {"LONG": COLORS["success"], "SHORT": COLORS["danger"],
                     "NO_TRADE": COLORS["text"], "REDUCE_ONLY": COLORS["warning"]}
        rows.append(html.Tr([
            html.Td(sym,       style={"color": COLORS["accent"]}),
            html.Td(action,    style={"color": color_map.get(action, COLORS["text"])}),
            html.Td(f"{prob_up:.3f}"),
            html.Td(conf),
            html.Td("✓" if allow else "✗",
                    style={"color": COLORS["success"] if allow else COLORS["danger"]}),
            html.Td(flags or "—", style={"fontSize": "11px"}),
        ]))

    if not rows:
        return html.Small("No data.", style={"color": COLORS["text"]})

    return html.Table([
        html.Thead(html.Tr([
            html.Th("Symbol"), html.Th("Action"), html.Th("P(up)"),
            html.Th("Conf"), html.Th("Allow"), html.Th("Flags"),
        ], style={"color": COLORS["text"]})),
        html.Tbody(rows),
    ], style={"width": "100%", "fontSize": "12px", "color": COLORS["text_light"]})


def _get_brier() -> float | None:
    try:
        from llm_agents.calibration import PredictionLogger
        pl = PredictionLogger(_LLM_CSV)
        return pl.get_rolling_brier()
    except Exception:
        return None


def _build_predictions_table() -> html.Div:
    if not _LLM_CSV.exists():
        return html.Small("data/llm_predictions.csv not found.", style={"color": COLORS["text"]})
    try:
        import pandas as pd
        df = pd.read_csv(_LLM_CSV).tail(20)
        if df.empty:
            return html.Small("No predictions yet.", style={"color": COLORS["text"]})
        cols = ["timestamp", "symbol", "final_action", "allow_trade",
                "final_prob_up", "max_risk_multiplier", "brier", "risk_flags"]
        cols = [c for c in cols if c in df.columns]
        return dash_table.DataTable(
            data=df[cols].to_dict("records"),
            columns=[{"name": c, "id": c} for c in cols],
            style_table={"overflowX": "auto", "maxHeight": "300px", "overflowY": "auto"},
            style_header={"backgroundColor": COLORS["card_bg"],
                          "color": COLORS["accent"], "fontSize": "11px"},
            style_cell={"backgroundColor": COLORS["bg"],
                        "color": COLORS["text_light"], "fontSize": "11px",
                        "border": f"1px solid {COLORS['grid']}"},
            page_size=20,
        )
    except Exception as exc:
        return html.Small(f"Error: {exc}", style={"color": COLORS["danger"]})
