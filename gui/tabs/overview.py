"""
overview.py — Tab 1: global KPIs + per-strategy cards + equity curve.
IDs overview-cards / overview-charts preserved for compatibility.
"""
import json
import time
from pathlib import Path

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, dcc, html

from gui.data_loader import load_decisions, load_fills, load_strategy_status
from gui.theme import COLORS, STRAT_COLORS, apply_dark_theme, no_data, stat_card

_REPO = Path(__file__).parent.parent.parent

# Bars needed before each strategy can generate signals (1 bar = 1 min)
_WARMUP_BARS = {
    "S8EMS":                 0,
    "OBImbalanceScalper":    0,
    "FundingArbitrage":      0,
    "FundingCarryHedged":    0,
    "SpotPerpBasis":         0,
    "MomentumLS":           14,
    "RotationMomentum":     14,
    "MeanReversionKalman":  10,
    "RelativeValue":        14,
    "BreakoutControlled":   20,
    "DonchianTrend":        20,
    "RSIBollingerReversion":20,
    "VolatilityRegimeBreakout": 14,
    "MetaAlpha":            20,
}

_ALL_STRATS = [
    "S8EMS", "MomentumLS", "BreakoutControlled",
    "MeanReversionKalman", "FundingArbitrage",
    "DonchianTrend", "RSIBollingerReversion",
    "RotationMomentum", "RelativeValue",
    "SpotPerpBasis", "FundingCarryHedged", "OBImbalanceScalper",
    "VolatilityRegimeBreakout", "MetaAlpha",
]
_DFLT_CAP = {s: 500 for s in _ALL_STRATS}
# Total capital = sum of all strategy allocations (computed dynamically)
_DFLT_TOTAL_CAP = float(sum(_DFLT_CAP.values()))  # 7000.0


def _total_capital(live_list: list) -> float:
    """Sum of capital_allocated_usd from live engine data, or fall back to defaults."""
    if live_list:
        total = sum(float(s.get("capital_allocated_usd", 0) or 0) for s in live_list)
        if total > 0:
            return total
    return _DFLT_TOTAL_CAP

def _build_equity_curve(fills: "pd.DataFrame", init_cap: float) -> "pd.DataFrame":
    """Build equity curve from fills. Returns DataFrame with {dt, equity}."""
    if fills.empty or "net" not in fills.columns:
        now = pd.Timestamp.utcnow()
        return pd.DataFrame({"dt": [now], "equity": [init_cap]})
    df = fills.copy()
    df["net"] = pd.to_numeric(df["net"], errors="coerce").fillna(0)
    if "dt" not in df.columns:
        df["dt"] = pd.to_datetime(df.get("ts", pd.Series(dtype="float64")), errors="coerce")
    df = df.dropna(subset=["dt"]).sort_values("dt")
    if df.empty:
        now = pd.Timestamp.utcnow()
        return pd.DataFrame({"dt": [now], "equity": [init_cap]})
    df["equity"] = init_cap + df["net"].cumsum()
    start = pd.DataFrame({
        "dt":     [df["dt"].iloc[0] - pd.Timedelta(seconds=1)],
        "equity": [init_cap],
    })
    return pd.concat([start, df[["dt", "equity"]]], ignore_index=True)


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


def _warmup_progress(name: str, n_trades: int, engine_start_ts: float, now: float):
    """Show warmup bar when strategy needs bar history and hasn't traded yet."""
    w = _WARMUP_BARS.get(name, 20)
    if w == 0 or n_trades > 0 or engine_start_ts <= 0:
        return None
    elapsed = (now - engine_start_ts) / 60.0
    if elapsed >= w:
        return None
    pct = min(elapsed / w * 100, 100)
    return html.Div([
        html.Div(f"Warmup {int(elapsed)}/{w} bars",
                 style={"fontSize": "9px", "color": COLORS["warning"],
                        "marginBottom": "1px", "fontFamily": "Consolas,monospace"}),
        html.Div(html.Div(style={
            "width": f"{pct:.0f}%", "height": "3px",
            "backgroundColor": COLORS["warning"], "borderRadius": "2px",
        }), style={"height": "3px", "backgroundColor": "#222", "borderRadius": "2px"}),
    ], style={"marginTop": "5px"})


_STATE_COLORS = {
    "ACTIVE":    COLORS["success"],
    "SUSPENDED": COLORS["warning"],
    "DISABLED":  COLORS["danger"],
    "KILLED":    COLORS["danger"],
}


def _strat_card(name, state, cap, cum_pnl, n_trades, wr, n_pos,
                engine_start_ts: float = 0.0, now: float = 0.0):
    sc     = _STATE_COLORS.get(state, COLORS["text"])
    accent = STRAT_COLORS.get(name, COLORS["accent"])
    pnl_txt   = f"${cum_pnl:+.2f}" if cum_pnl is not None else "N/A"
    pnl_color = (COLORS["success"] if (cum_pnl or 0) >= 0
                 else COLORS["danger"]) if cum_pnl is not None else COLORS["text"]
    wr_txt    = f"{wr:.0f}%" if wr is not None else "—"
    wr_color  = COLORS["success"] if (wr or 0) >= 50 else COLORS["warning"]
    warmup    = _warmup_progress(name, n_trades, engine_start_ts, now)

    body_children = [
        dbc.Row([
            dbc.Col(html.B(name, style={"color": accent, "fontSize": "11px"}),
                    width="auto"),
            dbc.Col(dbc.Badge(state, style={"backgroundColor": sc, "fontSize": "8px",
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
    ]
    if warmup:
        body_children.append(warmup)

    return dbc.Card(dbc.CardBody(body_children, style={"padding": "8px 10px"}),
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


def _count_recent_csv_rows(path: "Path", filter_fn,
                            window_s: float, now: float) -> int:
    """Count CSV rows with a timestamp within `window_s` of `now` matching
    `filter_fn(row)`. Returns 0 if file missing or unreadable."""
    try:
        if not path.exists():
            return 0
        # File mtime is a cheap proxy when timestamps are inconsistent.
        if (now - path.stat().st_mtime) > window_s * 2:
            return 0
        import csv as _csv
        cnt = 0
        with open(path, encoding="utf-8", errors="replace", newline="") as f:
            for row in _csv.DictReader(f):
                ts_raw = row.get("ts") or row.get("timestamp") or ""
                try:
                    # Try ISO format first, then plain float seconds
                    if "T" in ts_raw:
                        import datetime as _dt
                        t = _dt.datetime.fromisoformat(ts_raw).timestamp()
                    else:
                        t = float(ts_raw)
                except Exception:
                    continue
                if now - t <= window_s and filter_fn(row):
                    cnt += 1
        return cnt
    except Exception:
        return 0


def _build_alerts(live_list: list, now: float) -> list[dict]:
    """
    Phase 8: derive operational alerts from runtime files.
    Each alert is a dict {level, text} where level is "warning" or "critical".
    Read-only — never mutates engine state.
    """
    alerts: list[dict] = []

    # ── Phase-6 alerts ────────────────────────────────────────────────
    # A. Positions without stop / TP (data integrity)
    for s in live_list:
        sname = s.get("name", "?")
        for pos in s.get("open_positions", []):
            tp = pos.get("tp_price", 0)
            sp = pos.get("stop_price", 0)
            if (tp == 0 or tp is None) or (sp == 0 or sp is None):
                alerts.append({
                    "level": "critical",
                    "text":  (f"{sname}/{pos.get('symbol', '?')}: position has "
                              f"tp_price={tp} stop_price={sp} — no protective exits"),
                })

    # B. LLM-blocked trades in the last 5 minutes
    llm_path = _REPO / "logs" / "llm_decisions_v9.csv"
    n_blocked = _count_recent_csv_rows(
        llm_path,
        lambda r: (r.get("llm_decision") or "").upper() == "BLOCK",
        window_s=300, now=now,
    )
    if n_blocked > 0:
        alerts.append({
            "level": "warning",
            "text":  f"LLM: {n_blocked} BLOCK decision(s) in the last 5min",
        })

    # C. SanityCheckEngine rejections in the last 5 minutes
    rev_path = _REPO / "logs" / "risk_events.csv"
    n_sanity = _count_recent_csv_rows(
        rev_path,
        lambda r: ((r.get("block_reason") or "").startswith("sanity_")
                   and (r.get("allowed") in ("0", "False", "false"))),
        window_s=300, now=now,
    )
    if n_sanity > 0:
        alerts.append({
            "level": "warning" if n_sanity < 10 else "critical",
            "text":  f"SanityCheck: {n_sanity} rejection(s) in the last 5min",
        })

    # 1. Strategy suspended / killed and 2. drawdown > 15%
    for s in live_list:
        name = s.get("name", "?")
        state = (s.get("state") or "").upper()
        ledger = s.get("ledger") or {}
        if state in ("SUSPENDED", "suspended", "Suspended"):
            reason = ledger.get("suspend_reason", "") or s.get("suspend_reason", "")
            alerts.append({
                "level": "warning",
                "text":  f"{name}: SUSPENDED ({reason or 'see logs'})",
            })
        if state in ("KILLED", "killed"):
            reason = ledger.get("kill_reason", "") or s.get("kill_reason", "")
            alerts.append({
                "level": "critical",
                "text":  f"{name}: KILLED ({reason or 'see logs'})",
            })
        dd_pct = ledger.get("drawdown_pct")
        if isinstance(dd_pct, (int, float)) and dd_pct > 15.0:
            alerts.append({
                "level": "critical" if dd_pct > 20.0 else "warning",
                "text":  f"{name}: drawdown {dd_pct:.1f}% > 15%",
            })

        # 5. Zero-capital strategies with enabled=true (config warning).
        # Research-only / no-trade strategies are exempt — they intentionally
        # have capital=0 because they don't open positions.
        _RESEARCH_ONLY_STRATS = {
            "SecondsResearch", "SecondsResearchStrategy",
            "FundingArbEnhanced", "FundingArbitrageEnhanced",
        }
        cap = s.get("capital_allocated_usd")
        enabled = s.get("enabled", state not in ("DISABLED", "disabled"))
        max_pos = s.get("max_positions")
        is_research_only = (
            name in _RESEARCH_ONLY_STRATS
            or (isinstance(max_pos, (int, float)) and max_pos == 0)
            or bool((s.get("params") or {}).get("research_only"))
            or bool((s.get("params") or {}).get("trade_enabled") is False)
        )
        if enabled and isinstance(cap, (int, float)) and cap == 0 and not is_research_only:
            alerts.append({
                "level": "warning",
                "text":  f"{name}: enabled but capital_allocated_usd=0 (config check)",
            })

    # 3. Kill switch proximity — read engine_config.json or runtime kill-switch
    # status. For now we estimate from the aggregate realized PnL of all
    # strategies, compared against the daily-DD limit recorded in
    # engine_config.json (max_dd_daily_pct × total_capital).
    try:
        _ecfg_path = _REPO / "runtime" / "engine_config.json"
        ecfg = {}
        if _ecfg_path.exists():
            ecfg = json.load(open(_ecfg_path, encoding="utf-8"))
        total_cap = sum(float(s.get("capital_allocated_usd", 0) or 0)
                        for s in live_list) or _DFLT_TOTAL_CAP
        # Daily realized PnL aggregated from ledger snapshots
        daily_pnl = sum(
            float((s.get("ledger") or {}).get("daily_pnl", 0) or 0)
            for s in live_list
        )
        # Default daily-DD limit if engine_config doesn't expose it
        daily_dd_pct = float(ecfg.get("max_dd_daily_pct", 0.03))
        daily_limit_usd = total_cap * daily_dd_pct
        if daily_limit_usd > 0 and daily_pnl < 0:
            loss_ratio = abs(daily_pnl) / daily_limit_usd
            if loss_ratio >= 0.70:
                level = "critical" if loss_ratio >= 0.90 else "warning"
                alerts.append({
                    "level": level,
                    "text":  (f"Kill switch proximity: realized losses "
                              f"${abs(daily_pnl):.2f} = {loss_ratio*100:.0f}% "
                              f"of daily DD limit (${daily_limit_usd:.2f})"),
                })
    except Exception:
        pass

    # 4. WebSocket stale — last_heartbeat_ts > 30s ago from strategy_status mtime
    try:
        _sf = _REPO / "runtime" / "strategy_status.json"
        if _sf.exists():
            age = now - _sf.stat().st_mtime
            if age > 30:
                level = "critical" if age > 120 else "warning"
                alerts.append({
                    "level": level,
                    "text":  f"Status feed stale: last update {age:.0f}s ago (>30s)",
                })
    except Exception:
        pass

    return alerts


_ALERT_COLORS = {
    "warning":  COLORS["warning"],
    "critical": COLORS["danger"],
}


def _alerts_card(alerts: list[dict]) -> html.Div:
    """Render an alerts panel. Returns an empty div if no alerts."""
    if not alerts:
        return html.Div([
            html.P("ALERTES", style={"color": COLORS["text"],
                                      "letterSpacing": "2px",
                                      "fontSize": "10px",
                                      "marginBottom": "4px",
                                      "fontWeight": "700"}),
            html.Div("Aucune alerte active.",
                     style={"color": COLORS["success"],
                            "fontSize": "11px",
                            "fontFamily": "Consolas,monospace"}),
        ], style={"backgroundColor": COLORS["card_bg"], "border": _BDR,
                  "borderRadius": "4px", "padding": "8px 12px",
                  "marginTop": "10px"})

    rows = []
    for a in alerts:
        color = _ALERT_COLORS.get(a.get("level"), COLORS["text"])
        rows.append(html.Div([
            html.Span(a.get("level", "warning").upper(),
                      style={"color": color, "fontWeight": "700",
                             "fontSize": "10px", "marginRight": "8px",
                             "letterSpacing": "1px",
                             "fontFamily": "Consolas,monospace"}),
            html.Span(a.get("text", ""),
                      style={"color": COLORS["text_light"],
                             "fontSize": "11px",
                             "fontFamily": "Consolas,monospace"}),
        ], style={
            "padding":      "4px 8px",
            "marginBottom": "2px",
            "borderLeft":   f"3px solid {color}",
            "backgroundColor": "#0a0a0a",
            "borderRadius": "3px",
        }))

    return html.Div([
        html.P("ALERTES",
               style={"color": COLORS["warning"],
                      "letterSpacing": "2px", "fontSize": "10px",
                      "marginBottom": "4px", "fontWeight": "700"}),
        html.Div(rows),
    ], style={"backgroundColor": COLORS["card_bg"], "border": _BDR,
              "borderRadius": "4px", "padding": "8px 12px",
              "marginTop": "10px"})


def _health_row(live_list: list, decisions_today: int, now: float) -> html.Div:
    """Small health bar: engine feed age + active strategies + signals today."""
    # Feed freshness from strategy_status.json timestamp
    status_age = None
    for s in live_list:
        ts_val = s.get("ts") or s.get("updated_at")
        if ts_val:
            try:
                status_age = now - float(ts_val)
                break
            except Exception:
                pass

    # Also read directly from file ts field (written by _write_status)
    if status_age is None:
        try:
            _sf = _REPO / "runtime" / "strategy_status.json"
            if _sf.exists():
                status_age = now - _sf.stat().st_mtime
        except Exception:
            pass

    if status_age is None:
        feed_txt   = "—"
        feed_color = COLORS["text"]
    elif status_age < 15:
        feed_txt   = f"Feed OK ({status_age:.0f}s)"
        feed_color = COLORS["success"]
    elif status_age < 60:
        feed_txt   = f"Feed {status_age:.0f}s"
        feed_color = COLORS["warning"]
    else:
        feed_txt   = f"Feed stale ({status_age:.0f}s)"
        feed_color = COLORS["danger"]

    n_active = sum(1 for s in live_list if s.get("state") == "ACTIVE")
    n_total  = len(live_list) or len(_ALL_STRATS)

    def _pill(label, val, color):
        return html.Span([
            html.Span(label, style={"color": COLORS["text"], "fontSize": "10px"}),
            html.Span(f" {val}", style={"color": color, "fontWeight": "700",
                                         "fontSize": "10px", "fontFamily": "Consolas,monospace"}),
        ], style={"marginRight": "18px"})

    return html.Div([
        _pill("Data:", feed_txt, feed_color),
        _pill("Stratégies actives:", f"{n_active}/{n_total}", COLORS["accent"]),
        _pill("Signaux aujourd'hui:", str(decisions_today), COLORS["text_light"]),
    ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap",
              "padding": "5px 10px", "marginBottom": "8px",
              "backgroundColor": "#0a0a0a",
              "borderRadius": "4px", "border": f"1px solid {COLORS['grid']}"})


def register_callbacks(app) -> None:

    @app.callback(
        Output("overview-cards",  "children"),
        Output("overview-charts", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def update(_n):
        fills     = load_fills()
        decisions = load_decisions()
        live_list = load_strategy_status()
        live_map  = {s.get("name"): s for s in live_list}
        now       = time.time()

        # Total capital = sum of all strategy allocations (dynamic)
        init_cap = _total_capital(live_list)

        # Per-strategy stats from fills (always up-to-date)
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

        # ── Equity from fills directly (never from stale metrics CSV) ──
        total_pnl = 0.0
        pnl_day   = 0.0
        pnl_1h    = 0.0
        wins      = 0
        losses    = 0
        if not fills.empty and "net" in fills.columns:
            net       = fills["net"].fillna(0)
            total_pnl = float(net.sum())
            wins      = int((net > 0).sum())
            losses    = int((net < 0).sum())
            if "ts" in fills.columns:
                ts_col  = fills["ts"]
                try:
                    ts_num  = ts_col.astype(float)
                except Exception:
                    ts_num  = net * 0  # zeros
                pnl_day = float(net[ts_num > now - 86400].sum())
                pnl_1h  = float(net[ts_num > now - 3600].sum())
        equity = init_cap + total_pnl
        total  = wins + losses
        wr_txt = f"{100*wins/total:.1f}%" if total else "—"
        dd     = total_pnl / init_cap * 100 if init_cap else 0.0

        # Engine start time — for warmup progress bars
        engine_start_ts = 0.0
        try:
            _ecfg = _REPO / "runtime" / "engine_config.json"
            if _ecfg.exists():
                _ecfg_data = json.load(open(_ecfg, encoding="utf-8"))
                engine_start_ts = float(
                    _ecfg_data.get("started_at") or _ecfg_data.get("ts") or 0
                )
        except Exception:
            pass

        # Decisions since engine start (not 24h — avoids mixing old sessions)
        decisions_today = 0
        if not decisions.empty and "timestamp" in decisions.columns and engine_start_ts > 0:
            try:
                ts_num = pd.to_numeric(decisions["timestamp"], errors="coerce")
                decisions_today = int((ts_num > engine_start_ts).sum())
            except Exception:
                pass

        all_pos = []
        for s in live_list:
            for p in s.get("open_positions", []):
                p["strategy"] = s.get("name", "?")
                all_pos.append(p)

        # ── Global KPIs ───────────────────────────────────────────────
        g_cards = dbc.Row([
            dbc.Col(stat_card("Capital", f"${init_cap:.0f}", COLORS["text_light"])),
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
                # Use canonical state field; fall back to DISABLED when engine is off
                state = live.get("state", "DISABLED") if live else "DISABLED"
                n_pos = len(live.get("open_positions", [])) if live else 0
                st    = strat_stats.get(name, {})
                cols.append(dbc.Col(_strat_card(
                    name, state, cap,
                    st.get("pnl"), st.get("n", 0), st.get("wr"), n_pos,
                    engine_start_ts=engine_start_ts, now=now,
                ), width=True))
            return dbc.Row(cols, className="g-2", style={"marginTop": "8px"})

        def _grp(txt):
            return html.P(txt, style={"color": COLORS["text"], "fontSize": "9px",
                                       "letterSpacing": "2px", "textTransform": "uppercase",
                                       "marginTop": "12px", "marginBottom": "0"})

        existing  = ["S8EMS", "MomentumLS", "BreakoutControlled",
                     "MeanReversionKalman", "FundingArbitrage"]
        phase1    = ["DonchianTrend", "RSIBollingerReversion",
                     "RotationMomentum", "RelativeValue"]
        phase2    = ["SpotPerpBasis", "FundingCarryHedged", "OBImbalanceScalper",
                     "VolatilityRegimeBreakout", "MetaAlpha"]

        # Phase 8: operational alerts derived from runtime state
        alerts = _build_alerts(live_list, now)

        cards = html.Div([
            g_cards,
            _health_row(live_list, decisions_today, now),
            _alerts_card(alerts),
            _grp("Stratégies existantes"),
            _make_row(existing),
            _grp("Phase 1"),
            _make_row(phase1),
            _grp("Phase 2"),
            _make_row(phase2),
            _pos_table(all_pos),
        ])

        # ── Charts ────────────────────────────────────────────────────
        fig_eq = go.Figure()
        eq_df  = _build_equity_curve(fills, init_cap)
        last_eq = float(eq_df["equity"].iloc[-1]) if not eq_df.empty else init_cap
        lc = COLORS["success"] if last_eq >= init_cap else COLORS["danger"]
        r_ = int(lc[1:3], 16); g_ = int(lc[3:5], 16); b_ = int(lc[5:7], 16)
        fill_rgba = f"rgba({r_},{g_},{b_},0.08)"
        fig_eq.add_trace(go.Scatter(
            x=eq_df["dt"], y=eq_df["equity"],
            mode="lines", name="Equity",
            line=dict(color=lc, width=2),
            fill="tozeroy", fillcolor=fill_rgba,
        ))
        fig_eq.add_hline(y=init_cap, line_dash="dot",
                         line_color=COLORS["warning"], opacity=0.4,
                         annotation_text=f"Capital initial ${init_cap:.0f}")
        fig_eq.update_layout(title="Equity curve (fills)", showlegend=False, height=260)
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
