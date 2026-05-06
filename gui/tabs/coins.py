"""
coins.py — Tab 4: live candlestick + orderbook depth + trade simulator.

Live data: Hyperliquid public REST API (no auth required).
Main output stays at original ID 'coins-content' for Dash cache compat.
"""
import time as _time
from datetime import datetime, timezone

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
import requests
from dash import Input, Output, State, dcc, html

from gui.data_loader import load_fills
from gui.theme import COLORS, apply_dark_theme, no_data

_COINS = ["BTC", "ETH", "SOL", "HYPE", "AVAX", "LINK",
          "ARB", "OP", "AAVE", "LTC", "BCH", "XRP"]

_HL_URL   = "https://api.hyperliquid.xyz/info"
_TIMEOUT  = 4.0

_TAKER_BPS = 3.0
_MAKER_BPS = 0.3

_BDR  = f"1px solid {COLORS['grid']}"
_DARK = {"backgroundColor": COLORS["card_bg"], "border": _BDR,
         "borderRadius": "4px", "padding": "10px 12px"}
_BTN  = {"fontWeight": "700", "fontSize": "11px"}
_INP  = {"fontSize": "12px", "height": "30px", "backgroundColor": "#111",
         "color": COLORS["text_light"], "border": f"1px solid {COLORS['grid']}"}
_LBL  = {"color": COLORS["text"], "fontSize": "10px",
         "textTransform": "uppercase", "letterSpacing": "1px"}


# ── Hyperliquid helpers ───────────────────────────────────────────────────────

def _fetch_candles(coin: str, n: int = 120) -> list:
    now_ms   = int(_time.time() * 1000)
    start_ms = now_ms - n * 60 * 1000
    try:
        r = requests.post(_HL_URL,
                          json={"type": "candleSnapshot",
                                "req": {"coin": coin, "interval": "1m",
                                        "startTime": start_ms,
                                        "endTime":   now_ms}},
                          timeout=_TIMEOUT)
        data = r.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _fetch_book(coin: str) -> dict:
    try:
        r = requests.post(_HL_URL,
                          json={"type": "l2Book", "coin": coin},
                          timeout=_TIMEOUT)
        return r.json()
    except Exception:
        return {}


# ── Chart builders ────────────────────────────────────────────────────────────

def _candle_fig(candles: list, coin: str) -> go.Figure:
    fig = go.Figure()
    if not candles:
        fig.update_layout(title=f"{coin} — données indisponibles")
        apply_dark_theme(fig)
        return fig

    dates  = [datetime.fromtimestamp(c["t"] / 1000, tz=timezone.utc) for c in candles]
    opens  = [float(c["o"]) for c in candles]
    highs  = [float(c["h"]) for c in candles]
    lows   = [float(c["l"]) for c in candles]
    closes = [float(c["c"]) for c in candles]
    vols   = [float(c.get("v", 0)) for c in candles]

    fig.add_trace(go.Candlestick(
        x=dates, open=opens, high=highs, low=lows, close=closes,
        name=coin,
        increasing=dict(line=dict(color=COLORS["success"]),
                        fillcolor=COLORS["success"]),
        decreasing=dict(line=dict(color=COLORS["danger"]),
                        fillcolor=COLORS["danger"]),
    ))

    vol_colors = [COLORS["success"] if c >= o else COLORS["danger"]
                  for c, o in zip(closes, opens)]
    fig.add_trace(go.Bar(
        x=dates, y=vols, name="Vol",
        marker_color=vol_colors, opacity=0.35,
        yaxis="y2",
    ))

    last  = closes[-1] if closes else None
    title = f"{coin}  {last:.5g}" if last else coin

    fig.update_layout(
        title=title,
        xaxis_rangeslider_visible=False,
        yaxis=dict(title="Prix", side="left"),
        yaxis2=dict(title="Volume", overlaying="y", side="right",
                    showgrid=False, tickfont=dict(size=9)),
        legend=dict(orientation="h", y=1.02),
        height=320,
    )
    apply_dark_theme(fig)
    return fig


def _book_fig(book: dict, coin: str) -> go.Figure:
    fig = go.Figure()
    levels = book.get("levels", [])
    if len(levels) < 2:
        fig.update_layout(title=f"{coin} — carnet indisponible")
        apply_dark_theme(fig)
        return fig

    bids = [(float(l["px"]), float(l["sz"])) for l in levels[0][:20]]
    asks = [(float(l["px"]), float(l["sz"])) for l in levels[1][:20]]

    if not bids or not asks:
        apply_dark_theme(fig)
        return fig

    mid = (bids[0][0] + asks[0][0]) / 2

    bid_prices = [b[0] for b in bids]
    bid_cum    = []
    s = 0
    for _, sz in bids:
        s += sz
        bid_cum.append(s)

    ask_prices = [a[0] for a in asks]
    ask_cum    = []
    s = 0
    for _, sz in asks:
        s += sz
        ask_cum.append(s)

    fig.add_trace(go.Scatter(
        x=bid_prices, y=bid_cum,
        fill="tozeroy", mode="lines",
        line=dict(color=COLORS["success"], width=1),
        fillcolor="rgba(119,179,0,0.25)",
        name="Bids",
    ))
    fig.add_trace(go.Scatter(
        x=ask_prices, y=ask_cum,
        fill="tozeroy", mode="lines",
        line=dict(color=COLORS["danger"], width=1),
        fillcolor="rgba(204,0,0,0.25)",
        name="Asks",
    ))
    fig.add_vline(x=mid, line_dash="dot",
                  line_color=COLORS["warning"], opacity=0.7)

    spread_bps = (asks[0][0] - bids[0][0]) / mid * 10_000
    fig.update_layout(
        title=f"Carnet  bid {bids[0][0]:.5g}  ask {asks[0][0]:.5g}  "
              f"spread {spread_bps:.1f} bps",
        xaxis_title="Prix",
        yaxis_title="Taille cumulée",
        height=240,
    )
    apply_dark_theme(fig)
    return fig


def _sim_result(side: str, notional: float, entry: float,
                tp_pct: float, sl_pct: float) -> html.Div:
    if not entry or not notional:
        return html.Div()

    tp_pct_f = (tp_pct or 1.5) / 100
    sl_pct_f = (sl_pct or 1.0) / 100

    if side == "BUY":
        tp_price = entry * (1 + tp_pct_f)
        sl_price = entry * (1 - sl_pct_f)
    else:
        tp_price = entry * (1 - tp_pct_f)
        sl_price = entry * (1 + sl_pct_f)

    gross_tp = notional * tp_pct_f
    gross_sl = -notional * sl_pct_f

    entry_fee    = _TAKER_BPS * notional / 10_000
    exit_rebate  = _MAKER_BPS * notional / 10_000
    exit_taker   = _TAKER_BPS * notional / 10_000

    net_tp = gross_tp - entry_fee + exit_rebate
    net_sl = gross_sl - entry_fee - exit_taker

    size = notional / entry
    rr   = abs(net_tp / net_sl) if net_sl != 0 else 0

    def _row(label, value, color=None):
        return dbc.Row([
            dbc.Col(html.Small(label, style={**_LBL, "fontSize": "9px"}), width=7),
            dbc.Col(html.Span(value,
                              style={"color": color or COLORS["text_light"],
                                     "fontFamily": "Consolas, monospace",
                                     "fontSize": "11px", "fontWeight": "700"}),
                    width=5, className="text-end"),
        ], className="mb-1")

    sc = COLORS["success"] if side == "BUY" else COLORS["danger"]

    return html.Div([
        dbc.Row([
            dbc.Col(html.B(side, style={"color": sc, "fontSize": "13px"}), width="auto"),
            dbc.Col(html.Span(f"${notional:.0f}  @  {entry:.5g}",
                              style={"color": COLORS["text_light"],
                                     "fontFamily": "Consolas, monospace",
                                     "fontSize": "11px"}),
                    width="auto"),
        ], className="g-2 align-items-center mb-2"),

        _row("Taille (unités)",       f"{size:.6g}"),
        _row("TP prix",               f"{tp_price:.5g}",  COLORS["success"]),
        _row("SL prix",               f"{sl_price:.5g}",  COLORS["danger"]),

        html.Hr(style={"borderColor": COLORS["grid"], "margin": "6px 0"}),

        _row("Frais entrée (taker)",  f"−${entry_fee:.4f}", COLORS["danger"]),
        _row("Net si TP",             f"{net_tp:+.4f} $",
             COLORS["success"] if net_tp >= 0 else COLORS["danger"]),
        _row("Net si SL",             f"{net_sl:+.4f} $",  COLORS["danger"]),
        _row("Ratio R/R",             f"{rr:.2f}",
             COLORS["success"] if rr >= 1.5 else COLORS["warning"]),
    ], style={**_DARK, "marginTop": "8px"})


# ── Layout ────────────────────────────────────────────────────────────────────

def static_layout() -> html.Div:
    return html.Div([
        # Coin selector + mid price
        dbc.Row([
            dbc.Col([
                html.Label("Coin", style=_LBL),
                dcc.Dropdown(
                    id="coins-selector",
                    options=[{"label": c, "value": c} for c in _COINS],
                    value="BTC", clearable=False,
                    style={"backgroundColor": COLORS["card_bg"], "color": "#000"},
                ),
            ], width=3),
            dbc.Col(
                html.Div(id="coin-mid-badge",
                         style={"paddingTop": "22px", "fontSize": "18px",
                                "fontFamily": "Consolas, monospace",
                                "color": COLORS["accent"], "fontWeight": "700"}),
                width="auto",
            ),
        ], style={"marginBottom": "10px"}),

        dbc.Row([
            # ── Left: candlestick + orderbook (original coins-content) ─────
            dbc.Col(
                html.Div(id="coins-content"),   # ← preserved original ID
                width=7,
            ),

            # ── Right: simulator (static) + per-coin stats ────────────────
            dbc.Col([
                html.Div([
                    html.P("SIMULER UN TRADE", style={
                        "color": COLORS["warning"], "letterSpacing": "2px",
                        "fontSize": "10px", "textTransform": "uppercase",
                        "marginBottom": "8px", "fontWeight": "700",
                    }),
                    dbc.Row([
                        dbc.Col([
                            html.Label("Side", style=_LBL),
                            dbc.RadioItems(
                                id="sim-side",
                                options=[{"label": "BUY",  "value": "BUY"},
                                         {"label": "SELL", "value": "SELL"}],
                                value="BUY", inline=True,
                                style={"color": COLORS["text_light"], "fontSize": "12px"},
                            ),
                        ], width=12, className="mb-2"),
                    ]),
                    dbc.Row([
                        dbc.Col([
                            html.Label("Notional $", style=_LBL),
                            dbc.Input(id="sim-notional", type="number",
                                      value=100, min=1, step=10, style=_INP),
                        ], width=4),
                        dbc.Col([
                            html.Label("TP %", style=_LBL),
                            dbc.Input(id="sim-tp", type="number",
                                      value=1.5, min=0.1, step=0.1, style=_INP),
                        ], width=4),
                        dbc.Col([
                            html.Label("SL %", style=_LBL),
                            dbc.Input(id="sim-sl", type="number",
                                      value=1.0, min=0.1, step=0.1, style=_INP),
                        ], width=4),
                    ], className="mb-2"),
                    dbc.Button("▶ Calculer", id="sim-btn",
                               color="info", size="sm", style=_BTN),
                    html.Div(id="sim-result-div"),
                ], style=_DARK),

                html.Div(id="coin-stats-div", style={"marginTop": "8px"}),
            ], width=5),
        ]),
    ])


# ── Callbacks ─────────────────────────────────────────────────────────────────

def register_callbacks(app) -> None:

    # ── Live charts + mid + stats (auto-refresh) ──────────────────────────
    @app.callback(
        Output("coins-content",  "children"),   # candle + book charts
        Output("coin-mid-badge", "children"),
        Output("coin-stats-div", "children"),
        Input("coins-selector",  "value"),
        Input("refresh-interval","n_intervals"),
    )
    def _refresh(coin, _n):
        candles = _fetch_candles(coin, n=120)
        book    = _fetch_book(coin)

        fig_c = _candle_fig(candles, coin)
        fig_b = _book_fig(book, coin)

        # Mid price
        levels  = book.get("levels", [])
        mid_txt = ""
        if len(levels) >= 2 and levels[0] and levels[1]:
            try:
                bid_val = float(levels[0][0]["px"])
                ask_val = float(levels[1][0]["px"])
                mid_txt = f"{(bid_val + ask_val) / 2:.5g}"
            except Exception:
                pass

        charts_div = html.Div([
            dcc.Graph(figure=fig_c, config={"displayModeBar": False}),
            html.Div(dcc.Graph(figure=fig_b, config={"displayModeBar": False}),
                     style={"marginTop": "8px"}),
        ])

        # Per-coin fills stats
        stats_div = html.Div()
        fills = load_fills()
        if not fills.empty and "symbol" in fills.columns:
            cf = fills[fills["symbol"] == coin]
            if not cf.empty and "net" in cf.columns:
                cum  = float(cf["net"].sum())
                n    = len(cf)
                wins = int((cf["net"] > 0).sum())
                wr   = 100 * wins / n if n else 0
                pc   = COLORS["success"] if cum >= 0 else COLORS["danger"]
                stats_div = html.Div([
                    html.P("STATISTIQUES",
                           style={"color": COLORS["text"], "fontSize": "9px",
                                  "letterSpacing": "2px", "textTransform": "uppercase",
                                  "marginBottom": "4px"}),
                    dbc.Row([
                        dbc.Col([html.Div("PnL total", style=_LBL),
                                 html.Div(f"${cum:+.2f}",
                                          style={"color": pc, "fontWeight": "700",
                                                 "fontFamily": "Consolas, monospace"})],
                                width=4),
                        dbc.Col([html.Div("Trades", style=_LBL),
                                 html.Div(str(n),
                                          style={"color": COLORS["text_light"],
                                                 "fontWeight": "700",
                                                 "fontFamily": "Consolas, monospace"})],
                                width=4),
                        dbc.Col([html.Div("Win rate", style=_LBL),
                                 html.Div(f"{wr:.1f}%",
                                          style={"color": COLORS["accent"],
                                                 "fontWeight": "700",
                                                 "fontFamily": "Consolas, monospace"})],
                                width=4),
                    ]),
                ], style=_DARK)

        return charts_div, mid_txt, stats_div

    # ── Simulator (on button click) ────────────────────────────────────────
    @app.callback(
        Output("sim-result-div", "children"),
        Input("sim-btn",         "n_clicks"),
        State("coins-selector",  "value"),
        State("sim-side",        "value"),
        State("sim-notional",    "value"),
        State("sim-tp",          "value"),
        State("sim-sl",          "value"),
        prevent_initial_call=True,
    )
    def _simulate(_clicks, coin, side, notional, tp_pct, sl_pct):
        book   = _fetch_book(coin)
        levels = book.get("levels", [])
        entry  = None
        if len(levels) >= 2 and levels[0] and levels[1]:
            try:
                bid = float(levels[0][0]["px"])
                ask = float(levels[1][0]["px"])
                entry = ask if side == "BUY" else bid
            except Exception:
                pass

        if not entry:
            candles = _fetch_candles(coin, n=2)
            if candles:
                try:
                    entry = float(candles[-1]["c"])
                except Exception:
                    pass

        if not entry:
            return html.Div("Impossible de récupérer le prix live.",
                            style={"color": COLORS["danger"], "fontSize": "11px",
                                   "marginTop": "6px"})

        return _sim_result(side or "BUY", float(notional or 100),
                           entry, float(tp_pct or 1.5), float(sl_pct or 1.0))
