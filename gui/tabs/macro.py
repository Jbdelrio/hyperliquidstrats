"""
gui/tabs/macro.py — onglet "📅 Macro" : vue calendrier des fenêtres de blackout.

Liste les prochains gros events (NFP/CPI/FOMC/discours Fed) et leur fenêtre de
STOP+FREEZE [start→end] (UTC), avec compte à rebours. Thème Cyborg, refresh global.
Lecture seule (le moteur applique le blackout ; cf. risk/macro_calendar.py).
"""
from __future__ import annotations

from datetime import datetime, timezone

from dash import Input, Output, html

from gui.theme import COLORS

M = "JetBrains Mono, Consolas, monospace"
_CAL = None
_TYPE_LABEL = {"NFP": "NFP (emploi US)", "CPI": "US CPI (inflation)",
               "FOMC": "FOMC — décision taux", "FOMC_SPEECH": "Discours Fed (presser)"}


def _calendar():
    global _CAL
    if _CAL is None:
        try:
            from risk.macro_calendar import MacroCalendar
            _CAL = MacroCalendar()
        except Exception:
            _CAL = None
    return _CAL


def static_layout() -> list:
    return [
        html.Div([
            html.Span("📅 CALENDRIER MACRO", style={"fontWeight": "700", "fontSize": "16px",
                                                  "color": COLORS["accent"], "letterSpacing": "1px"}),
            html.Span("STOP (liquide) + FREEZE auto autour des events — 15 min avant/après "
                      "(discours Fed : +30 min après)",
                      style={"marginLeft": "12px", "fontSize": "10px", "color": COLORS["text"]}),
        ], style={"marginBottom": "10px"}),
        html.Div(id="macro-status", style={"marginBottom": "10px", "fontFamily": M,
                                           "fontSize": "13px", "padding": "8px 12px",
                                           "borderRadius": "6px"}),
        html.Div(id="macro-table"),
        html.Div("NFP généré automatiquement (1er vendredi 08:30 ET). CPI/FOMC depuis "
                 "config/macro_events.json (à tenir à jour : bls.gov, federalreserve.gov). "
                 "Heures converties UTC (DST géré).",
                 style={"marginTop": "12px", "fontSize": "10px", "color": COLORS["text"],
                        "fontFamily": M, "opacity": 0.7}),
    ]


def _fmt_eta(s: int) -> str:
    if s < 0:
        return "en cours"
    d, r = divmod(s, 86400); h, r = divmod(r, 3600); m = r // 60
    return (f"{d}j {h}h{m:02d}" if d else (f"{h}h{m:02d}" if h else f"{m} min"))


def _cell(v, **st):
    base = {"padding": "6px 10px", "fontFamily": M, "fontSize": "11px",
            "borderBottom": f"1px solid {COLORS['grid']}", "textAlign": "left"}
    base.update(st)
    return html.Td(v, style=base)


def _short(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%a %d %b %H:%M")
    except Exception:
        return iso


def register_callbacks(app) -> None:
    @app.callback(
        Output("macro-status", "children"),
        Output("macro-status", "style"),
        Output("macro-table", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def _update(_n):
        cal = _calendar()
        base_style = {"fontFamily": M, "fontSize": "13px", "padding": "8px 12px",
                      "borderRadius": "6px", "backgroundColor": "#0c1018"}
        if cal is None:
            return ("Calendrier indisponible.", base_style, html.Div())
        try:
            cal.reload()
        except Exception:
            pass
        st = cal.status()
        if st["in_blackout"]:
            banner = f"⏸ BLACKOUT EN COURS — {st['event']} ({st['phase']}) → STOP+FREEZE jusqu'à {_short(st['blackout_until'])} UTC"
            col = COLORS["danger"]
        else:
            banner = (f"✅ Trading actif · prochain : {st['next_event']} dans "
                      f"{_fmt_eta(st['seconds_to_next'] or 0)} "
                      f"(freeze dans {_fmt_eta(st['seconds_to_blackout'] or 0)})")
            col = COLORS["success"]
        style = {**base_style, "color": col, "border": f"1px solid {col}55"}

        cols = ["Événement", "Heure (UTC)", "Fenêtre STOP+FREEZE", "Dans"]
        header = html.Tr([html.Th(c, style={"padding": "6px 10px", "fontFamily": M,
                                            "fontSize": "10px", "color": COLORS["accent"],
                                            "textAlign": "left", "textTransform": "uppercase",
                                            "borderBottom": f"1px solid {COLORS['accent']}55"})
                          for c in cols])
        rows = []
        for e in cal.upcoming(limit=20):
            active = e["active"]
            rcol = COLORS["danger"] if active else COLORS["text"]
            rows.append(html.Tr([
                _cell(_TYPE_LABEL.get(e["type"], e["type"]),
                      color=COLORS["text_light"], fontWeight="700"),
                _cell(_short(e["when_utc"])),
                _cell(f"{_short(e['start_utc'])} → {datetime.fromisoformat(e['end_utc']).strftime('%H:%M')}"),
                _cell("⏸ EN COURS" if active else _fmt_eta(e["seconds_to_start"]),
                      color=rcol, fontWeight="700" if active else "400"),
            ]))
        table = html.Table([html.Thead(header), html.Tbody(rows)],
                           style={"width": "100%", "borderCollapse": "collapse",
                                  "backgroundColor": COLORS["card_bg"]})
        return banner, style, table
