"""
gui/tabs/exchanges.py — Multi-exchange status dashboard tab.

Shows enabled exchanges, their data feed status, spread/funding comparison.
Graceful degradation: works even if only Hyperliquid is available.
"""
from __future__ import annotations

import os
from pathlib import Path

import dash_bootstrap_components as dbc
from dash import Input, Output, html

from gui.theme import COLORS

_EXCHANGE_NAMES = ["hyperliquid", "binance", "bitget"]
_DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL"]


def static_layout() -> list:
    return [
        html.H5("Exchanges", style={"color": COLORS["accent"], "marginBottom": "12px"}),
        dbc.Row([
            dbc.Col(_config_card(), width=4),
            dbc.Col(_status_card(), width=8),
        ], className="mb-3"),
        dbc.Row([
            dbc.Col(_comparison_card(), width=12),
        ]),
    ]


def _config_card():
    return dbc.Card([
        dbc.CardHeader("Configuration", style={"color": COLORS["accent"]}),
        dbc.CardBody(html.Div(id="exchanges-config-body")),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"})


def _status_card():
    return dbc.Card([
        dbc.CardHeader("Exchange Status", style={"color": COLORS["accent"]}),
        dbc.CardBody(html.Div(id="exchanges-status-body")),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"})


def _comparison_card():
    return dbc.Card([
        dbc.CardHeader("Cross-Exchange Comparison (live ticker)",
                       style={"color": COLORS["accent"]}),
        dbc.CardBody(html.Div(id="exchanges-comparison-body")),
    ], style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"})


def register_callbacks(app) -> None:

    @app.callback(
        Output("exchanges-config-body", "children"),
        Output("exchanges-status-body", "children"),
        Output("exchanges-comparison-body", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def _update(_n):
        config_out     = _build_config()
        status_out     = _build_status()
        comparison_out = _build_comparison()
        return config_out, status_out, comparison_out


def _build_config() -> html.Div:
    default_ex  = os.environ.get("DEFAULT_EXCHANGE", "hyperliquid")
    enabled_str = os.environ.get("ENABLED_EXCHANGES", "hyperliquid")
    global_live = os.environ.get("GLOBAL_LIVE_TRADING", "false")
    cross_en    = os.environ.get("CROSS_EXCHANGE_ENABLED", "false")

    items = [
        _kv("Default exchange",   default_ex),
        _kv("Enabled exchanges",  enabled_str),
        _kv("Global live trading", global_live,
            COLORS["danger"] if global_live.lower() in ("true", "1") else COLORS["success"]),
        _kv("Cross-exchange",      cross_en),
        html.Hr(style={"borderColor": COLORS["grid"]}),
    ]
    for ex in _EXCHANGE_NAMES:
        ex_enabled = os.environ.get(f"{ex.upper()}_ENABLED", "false")
        ex_live    = os.environ.get(f"{ex.upper()}_LIVE_TRADING", "false")
        testnet    = os.environ.get(f"{ex.upper()}_TESTNET", "true")
        items.append(_kv(
            ex.capitalize(),
            f"{'ON' if ex_enabled.lower() in ('true','1') else 'OFF'}"
            f" | live={'YES' if ex_live.lower() in ('true','1') else 'NO'}"
            f" | testnet={'YES' if testnet.lower() in ('true','1') else 'NO'}",
            COLORS["success"] if ex_enabled.lower() in ("true", "1") else COLORS["text"],
        ))

    return html.Div(items)


def _build_status() -> html.Div:
    rows = []
    enabled_str = os.environ.get("ENABLED_EXCHANGES", "hyperliquid")
    enabled = [e.strip().lower() for e in enabled_str.split(",") if e.strip()]

    for name in _EXCHANGE_NAMES:
        is_enabled = name in enabled
        live_key   = f"{name.upper()}_LIVE_TRADING"
        is_live    = os.environ.get(live_key, "false").lower() in ("true", "1")

        if is_enabled:
            ticker_ok = _check_ticker(name)
            mode = ("LIVE" if is_live else "DATA-ONLY") if ticker_ok else "ERROR"
            color = (COLORS["danger"] if is_live else
                     COLORS["success"] if ticker_ok else COLORS["warning"])
        else:
            mode  = "DISABLED"
            color = COLORS["text"]

        rows.append(html.Tr([
            html.Td(name.capitalize(), style={"color": COLORS["text_light"]}),
            html.Td(mode, style={"color": color, "fontWeight": "600"}),
        ]))

    return html.Table([
        html.Thead(html.Tr([html.Th("Exchange"), html.Th("Mode")],
                           style={"color": COLORS["text"]})),
        html.Tbody(rows),
    ], style={"width": "100%", "fontSize": "12px"})


def _build_comparison() -> html.Div:
    symbols = [s.strip() for s in
               os.environ.get("CROSS_EXCHANGE_SYMBOLS", "BTC,ETH,SOL").split(",")
               if s.strip()][:3]

    enabled_str = os.environ.get("ENABLED_EXCHANGES", "hyperliquid")
    enabled = [e.strip().lower() for e in enabled_str.split(",") if e.strip()]

    if len(enabled) <= 1:
        return html.Small(
            "Only one exchange enabled. Add BINANCE_ENABLED=true or BITGET_ENABLED=true "
            "to ENABLED_EXCHANGES for cross-exchange comparison.",
            style={"color": COLORS["text"]},
        )

    header = html.Tr(
        [html.Th("Symbol")] + [html.Th(ex.capitalize()) for ex in enabled],
        style={"color": COLORS["text"]},
    )
    rows = []
    for sym in symbols:
        cells = [html.Td(sym, style={"color": COLORS["accent"]})]
        for ex_name in enabled:
            ticker = _get_ticker(ex_name, sym)
            if ticker:
                cells.append(html.Td(
                    f"${ticker['mid']:.2f} | {ticker['spread_bps']:.1f}bps",
                    style={"color": COLORS["text_light"], "fontSize": "11px"},
                ))
            else:
                cells.append(html.Td("—", style={"color": COLORS["text"]}))
        rows.append(html.Tr(cells))

    return html.Table(
        [html.Thead(header), html.Tbody(rows)],
        style={"width": "100%", "fontSize": "12px"},
    )


def _check_ticker(exchange_name: str) -> bool:
    try:
        from exchanges.factory import get_exchange
        adapter = get_exchange(exchange_name)
        return adapter is not None
    except Exception:
        return False


def _get_ticker(exchange_name: str, symbol: str) -> dict | None:
    try:
        from exchanges.factory import get_exchange
        adapter = get_exchange(exchange_name)
        if adapter is None:
            return None
        ticker = adapter.get_ticker(symbol)
        if ticker is None:
            return None
        return {"mid": ticker.mid or 0.0, "spread_bps": ticker.spread_bps or 0.0}
    except Exception:
        return None


def _kv(label: str, value: str, color: str = None) -> html.Div:
    return html.Div([
        html.Span(f"{label}: ", style={"color": COLORS["text"], "fontWeight": "600"}),
        html.Span(value, style={"color": color or COLORS["text_light"]}),
    ], style={"marginBottom": "4px"})
