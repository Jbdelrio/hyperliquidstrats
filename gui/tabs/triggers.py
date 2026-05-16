"""
triggers.py — "Why isn't X trading?" panel.

Answers three concrete questions, refreshed every 5 s :

1. **Market state per coin** — current spread, OFI, volume, toxicity,
   liquidity, plus a green/red verdict on whether each MarketQualityGate
   rule would currently pass.

2. **Block reasons (last 5 min)** — top reasons decisions get rejected,
   so you know if it's spread, ofi, throttle, sanity, …

3. **Live decision feed** — last 20 decisions per strategy with their
   reason, so you can see what each strategy is doing tick-by-tick.

The tab reads :
- `runtime/data_feed_status.json`  (written by the engine each 5 s)
- `runtime/strategy_status.json`
- `logs/decisions_v9.csv`
- `logs/risk_events.csv`
"""
import json
import math
import time
from pathlib import Path

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, dash_table, dcc, html

from gui.theme import COLORS

_REPO = Path(__file__).resolve().parents[2]
_DATA_FEED_STATUS = _REPO / "runtime" / "data_feed_status.json"
_DECISIONS = _REPO / "logs" / "decisions_v9.csv"
_RISK_EVENTS = _REPO / "logs" / "risk_events.csv"


# ---------------------------------------------------------------------------
# Static layout
# ---------------------------------------------------------------------------

def static_layout() -> html.Div:
    return html.Div([
        dbc.Row([
            dbc.Col(html.H5("🎯 Triggers — Why is the bot (not) trading?",
                            style={"color": COLORS["accent"]}), width=12),
            dbc.Col(html.Small("Refreshed every 5s. Reads runtime/data_feed_status.json + recent CSV logs.",
                               style={"color": COLORS["text"]}), width=12),
        ], className="mb-3"),

        dbc.Row([
            dbc.Col([
                html.H6("1. Market state per coin (live)",
                        style={"color": COLORS["accent"], "marginTop": "8px"}),
                html.Div(id="triggers-market-state"),
            ], width=12),
        ], className="mb-4"),

        dbc.Row([
            dbc.Col([
                html.H6("2. Top block reasons (last 5 min)",
                        style={"color": COLORS["accent"]}),
                html.Div(id="triggers-block-reasons"),
            ], width=6),
            dbc.Col([
                html.H6("3. Latest decisions per strategy",
                        style={"color": COLORS["accent"]}),
                html.Div(id="triggers-recent-decisions"),
            ], width=6),
        ], className="mb-4"),

        dbc.Row([
            dbc.Col([
                html.H6("4. Gate stats since engine start",
                        style={"color": COLORS["accent"]}),
                html.Div(id="triggers-gate-stats"),
            ], width=12),
        ]),
    ])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_data_feed_status() -> dict:
    if not _DATA_FEED_STATUS.exists():
        return {}
    try:
        return json.loads(_DATA_FEED_STATUS.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _verdict_cell(ok: bool, label_ok: str = "OK", label_bad: str = "BLOCK") -> str:
    return f"✅ {label_ok}" if ok else f"❌ {label_bad}"


def _fmt(v, digits=3):
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "✓" if v else "✗"
    try:
        v = float(v)
    except (TypeError, ValueError):
        return str(v)
    if math.isnan(v) or math.isinf(v):
        return "—"
    if abs(v) >= 100:
        return f"{v:.1f}"
    return f"{v:.{digits}f}"


# ---------------------------------------------------------------------------
# Section 1: market state per coin
# ---------------------------------------------------------------------------

def _build_market_state_table(status: dict) -> html.Div:
    feats_by_sym = (status.get("seconds_features") or {})
    if not feats_by_sym:
        return html.Div("En attente du SecondsFeatureEngine "
                        "(warmup ~90s après démarrage).",
                        style={"color": COLORS["text"]})

    # Pull MQG thresholds from runtime/data_feed_status if exposed;
    # else fall back to safe defaults.
    mqg_cfg = (status.get("market_quality_gate_cfg") or {})
    max_spread_map = mqg_cfg.get("max_spread_bps_by_symbol", {})
    min_vol_map = mqg_cfg.get("min_volume_30s_usd_by_symbol", {})
    max_book_age = float(mqg_cfg.get("max_book_age_s", 3.0))
    max_tox = float(mqg_cfg.get("max_toxicity_score", 0.80))
    min_liq = float(mqg_cfg.get("min_liquidity_score", 0.25))
    ofi_thr = float(mqg_cfg.get("ofi_block_threshold", 0.30))

    rows = []
    for sym, f in feats_by_sym.items():
        mid = f.get("mid")
        spread = f.get("spread_bps")
        book_age = f.get("book_age_s")
        ofi30 = f.get("ofi_30s")
        vol30 = f.get("trade_volume_30s")
        tox = f.get("toxicity_score")
        liq = f.get("liquidity_score")
        enough = f.get("enough_data", False)

        max_spread = float(max_spread_map.get(sym, max_spread_map.get("DEFAULT", 18.0)))
        min_vol = float(min_vol_map.get(sym, min_vol_map.get("DEFAULT", 1000.0)))

        # Per-rule verdicts
        v_warmup = enough
        v_book = isinstance(book_age, (int, float)) and book_age <= max_book_age
        v_spread = isinstance(spread, (int, float)) and spread <= max_spread
        v_vol = isinstance(vol30, (int, float)) and vol30 >= min_vol
        v_tox = (tox is None) or (tox <= max_tox)
        v_liq = (liq is None) or (liq >= min_liq)
        v_ofi_long = (ofi30 is None) or (ofi30 >= -ofi_thr)
        v_ofi_short = (ofi30 is None) or (ofi30 <= ofi_thr)

        all_ok_long = all([v_warmup, v_book, v_spread, v_vol, v_tox, v_liq, v_ofi_long])
        all_ok_short = all([v_warmup, v_book, v_spread, v_vol, v_tox, v_liq, v_ofi_short])

        rows.append({
            "Coin": sym,
            "Mid": _fmt(mid),
            f"Spread (≤{max_spread:.0f}bps)": f"{_fmt(spread)}  {_verdict_cell(bool(v_spread))}",
            f"Vol 30s (≥${min_vol:,.0f})": f"${_fmt(vol30,0)}  {_verdict_cell(bool(v_vol))}",
            f"OFI 30s (|x|<{ofi_thr})": f"{_fmt(ofi30)}",
            f"Toxicity (≤{max_tox})": f"{_fmt(tox)}  {_verdict_cell(bool(v_tox))}",
            f"Liquidity (≥{min_liq})": f"{_fmt(liq)}  {_verdict_cell(bool(v_liq))}",
            f"Book age (≤{max_book_age:.1f}s)": f"{_fmt(book_age,1)}  {_verdict_cell(bool(v_book))}",
            "LONG OK?": _verdict_cell(all_ok_long, "trade", "blocked"),
            "SHORT OK?": _verdict_cell(all_ok_short, "trade", "blocked"),
        })

    if not rows:
        return html.Div("Pas de coins avec features.",
                        style={"color": COLORS["text"]})
    return dash_table.DataTable(
        data=rows,
        columns=[{"name": k, "id": k} for k in rows[0].keys()],
        style_cell={"backgroundColor": COLORS["card_bg"],
                    "color": COLORS["text_light"],
                    "fontFamily": "monospace", "fontSize": "12px",
                    "padding": "6px"},
        style_header={"backgroundColor": COLORS["bg"],
                      "color": COLORS["accent"],
                      "fontWeight": "bold"},
        page_size=20,
    )


# ---------------------------------------------------------------------------
# Section 2: top block reasons
# ---------------------------------------------------------------------------

def _build_block_reasons() -> html.Div:
    now = time.time()
    cutoff_ts = now - 300.0   # last 5 min

    rows = []
    if _DECISIONS.exists():
        try:
            df = pd.read_csv(_DECISIONS).tail(20_000)
            if "reason" in df.columns:
                # Convert timestamp column to seconds — try common names.
                ts_col = None
                for c in ("timestamp", "ts"):
                    if c in df.columns:
                        ts_col = c
                        break
                if ts_col is not None:
                    df["_ts"] = pd.to_numeric(df[ts_col], errors="coerce")
                    df = df[df["_ts"] >= cutoff_ts]
                # decisions SKIP : reason like "market_quality:spread_too_wide:7.5>5"
                if "decision" in df.columns:
                    df = df[df["decision"].astype(str).str.upper() == "SKIP"]
                df["reason_head"] = (df["reason"].astype(str)
                                     .str.split(":", n=2)
                                     .str[0])
                df["reason_sub"] = (df["reason"].astype(str)
                                    .str.split(":", n=2)
                                    .str[:2].str.join(":"))
                counts = df.groupby(["reason_head", "reason_sub"]).size().reset_index(name="count")
                counts = counts.sort_values("count", ascending=False).head(15)
                rows = counts.to_dict("records")
        except Exception as e:
            return html.Div(f"Erreur de parsing decisions_v9.csv: {e}",
                            style={"color": COLORS["warning"]})

    if not rows:
        return html.Div("Aucun blocage récent ou logs/decisions_v9.csv vide.",
                        style={"color": COLORS["text"]})

    return dash_table.DataTable(
        data=rows,
        columns=[{"name": k, "id": k} for k in rows[0].keys()],
        style_cell={"backgroundColor": COLORS["card_bg"],
                    "color": COLORS["text_light"],
                    "fontFamily": "monospace", "fontSize": "12px",
                    "padding": "6px"},
        style_header={"backgroundColor": COLORS["bg"],
                      "color": COLORS["accent"],
                      "fontWeight": "bold"},
        page_size=15,
    )


# ---------------------------------------------------------------------------
# Section 3: latest decisions per strategy
# ---------------------------------------------------------------------------

def _build_recent_decisions() -> html.Div:
    if not _DECISIONS.exists():
        return html.Div("logs/decisions_v9.csv absent.",
                        style={"color": COLORS["text"]})
    try:
        df = pd.read_csv(_DECISIONS).tail(2000)
    except Exception as e:
        return html.Div(f"Erreur: {e}", style={"color": COLORS["warning"]})

    if df.empty:
        return html.Div("Pas de décisions encore.",
                        style={"color": COLORS["text"]})

    # Last 3 entries per strategy
    if "strategy" not in df.columns:
        return html.Div("Colonne 'strategy' absente.", style={"color": COLORS["text"]})
    keep_cols = [c for c in ("strategy", "symbol", "decision", "reason", "timestamp")
                 if c in df.columns]
    grouped = (df.groupby("strategy", group_keys=False)
                 .apply(lambda g: g.tail(3))[keep_cols])
    if grouped.empty:
        return html.Div("Pas de décisions par stratégie.",
                        style={"color": COLORS["text"]})

    return dash_table.DataTable(
        data=grouped.to_dict("records"),
        columns=[{"name": c, "id": c} for c in keep_cols],
        style_cell={"backgroundColor": COLORS["card_bg"],
                    "color": COLORS["text_light"],
                    "fontFamily": "monospace", "fontSize": "11px",
                    "padding": "5px",
                    "whiteSpace": "normal", "height": "auto"},
        style_header={"backgroundColor": COLORS["bg"],
                      "color": COLORS["accent"],
                      "fontWeight": "bold"},
        page_size=30,
    )


# ---------------------------------------------------------------------------
# Section 4: gate stats from runtime/data_feed_status.json
# ---------------------------------------------------------------------------

def _build_gate_stats(status: dict) -> html.Div:
    cards = []
    mqg = status.get("market_quality_gate_stats") or {}
    if mqg:
        cards.append(_stat_card(
            "MarketQualityGate",
            evaluated=mqg.get("total_evaluated", 0),
            blocked=mqg.get("total_blocked", 0),
            breakdown=mqg.get("blocks_by_reason", {}),
        ))
    thr = status.get("decision_throttle_stats") or {}
    if thr:
        cards.append(_stat_card(
            "DecisionThrottle",
            evaluated=thr.get("total_evaluated", 0),
            blocked=thr.get("total_blocked", 0),
            breakdown=thr.get("blocks_by_reason", {}),
        ))
    health = status.get("data_feed_health") or {}
    if health:
        cards.append(_feed_card(health))
    if not cards:
        return html.Div("En attente du premier dump runtime/data_feed_status.json.",
                        style={"color": COLORS["text"]})
    return dbc.Row([dbc.Col(c, width=4) for c in cards])


def _stat_card(title: str, evaluated: int, blocked: int, breakdown: dict) -> html.Div:
    ratio = (blocked / evaluated * 100.0) if evaluated > 0 else 0.0
    rows = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)[:8]
    breakdown_lines = [html.Div(f"{reason}: {count}",
                                style={"fontSize": "11px",
                                       "color": COLORS["text"]})
                       for reason, count in rows]
    return dbc.Card([
        dbc.CardHeader(title, style={"color": COLORS["accent"],
                                     "backgroundColor": COLORS["bg"]}),
        dbc.CardBody([
            html.Div(f"Evaluated: {evaluated}", style={"color": COLORS["text_light"]}),
            html.Div(f"Blocked:   {blocked} ({ratio:.0f}%)",
                     style={"color": COLORS["warning"] if ratio > 50
                            else COLORS["text_light"]}),
            html.Hr(style={"margin": "6px 0"}),
            html.Div("Top reasons:", style={"color": COLORS["accent"],
                                            "fontSize": "11px"}),
            *breakdown_lines,
        ]),
    ], style={"backgroundColor": COLORS["card_bg"],
              "border": f"1px solid {COLORS['grid']}"})


def _feed_card(health: dict) -> html.Div:
    items = []
    items.append(html.Div(f"book_updates: {health.get('book_updates_count', 0)}",
                          style={"color": COLORS["text"]}))
    items.append(html.Div(f"trade_events: {health.get('trade_events_count', 0)}",
                          style={"color": COLORS["text"]}))
    drops = health.get("queue_drops", 0)
    color = COLORS["danger"] if drops > 0 else COLORS["text"]
    items.append(html.Div(f"queue_drops: {drops}", style={"color": color}))
    crossed = health.get("crossed_book_count", 0)
    color = COLORS["danger"] if crossed > 0 else COLORS["text"]
    items.append(html.Div(f"crossed_books: {crossed}", style={"color": color}))
    items.append(html.Div(f"reconnections: {health.get('reconnections', 0)}",
                          style={"color": COLORS["text"]}))
    return dbc.Card([
        dbc.CardHeader("Data feed health",
                       style={"color": COLORS["accent"],
                              "backgroundColor": COLORS["bg"]}),
        dbc.CardBody(items),
    ], style={"backgroundColor": COLORS["card_bg"],
              "border": f"1px solid {COLORS['grid']}"})


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

def register_callbacks(app) -> None:

    @app.callback(
        Output("triggers-market-state",     "children"),
        Output("triggers-block-reasons",    "children"),
        Output("triggers-recent-decisions", "children"),
        Output("triggers-gate-stats",       "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def _refresh(_n):
        status = _load_data_feed_status()
        return (
            _build_market_state_table(status),
            _build_block_reasons(),
            _build_recent_decisions(),
            _build_gate_stats(status),
        )
