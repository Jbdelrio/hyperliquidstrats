"""
strategies.py — Tab Stratégies.

Chaque stratégie = une carte avec :
  • En-tête  : nom, badge statut, capital, coins
  • Boutons  : Activer / Désactiver / Reprendre / Reset / Flatten
  • DataTable éditable en ligne (une par stratégie, ID fixe)
  • Pied     : champ Capital + Appliquer les paramètres

IDs fixes, zéro pattern-matching, zéro dynamic component.
"""
import time

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, dash_table, dcc, html

from gui.control_api import ControlAPI
from gui.data_loader import load_strategy_status
from gui.engine_controller import engine_ctrl
from gui.theme import COLORS

_api = ControlAPI()

_ALL = ["S8EMS", "MomentumLS", "BreakoutControlled",
        "MeanReversionKalman", "FundingArbitrage"]

_DEF = {
    "S8EMS":              {"capital": 100, "enabled": False,
                           "params": {"min_spread_bps": 4.0, "max_hold_s": 60,
                                      "stop_loss_bps": 30, "max_leverage": 5,
                                      "quote_refresh_s": 5.0, "bouchaud_decay_s": 30,
                                      "wavelet_threshold": 3.0}},
    "MomentumLS":         {"capital": 150, "enabled": True,
                           "params": {"rerank_seconds": 300, "top_k_long": 4,
                                      "bottom_k_short": 4, "score_threshold": 75,
                                      "stop_loss_pct": 2.5, "take_profit_pct": 3.0,
                                      "trailing_stop_pct": 1.5, "max_hold_hours": 4}},
    "BreakoutControlled": {"capital": 100, "enabled": True,
                           "params": {"lookback_bars": 60, "bo_max_pct": 4.0,
                                      "vr_min": 1.5, "take_profit_pct": 5.0,
                                      "stop_below_resistance_pct": 0.5,
                                      "max_hold_hours": 2}},
    "MeanReversionKalman":{"capital": 100, "enabled": False,
                           "params": {"warmup_seconds": 300, "z_entry": 2.0,
                                      "z_exit": 0.0, "z_stop": 3.5,
                                      "vol_max_pct_per_min": 0.15,
                                      "max_hold_minutes": 30}},
    "FundingArbitrage":   {"capital":  50, "enabled": False,
                           "params": {"funding_entry_threshold_pct_per_hour": 0.03,
                                      "funding_exit_threshold_pct_per_hour": 0.005,
                                      "stop_loss_pct": 3.0, "max_hold_cycles": 3}},
}

_BTN = {"fontWeight": "700", "fontSize": "11px"}
_BDR = f"1px solid {COLORS['grid']}"

_TH = {"backgroundColor": "#060606", "color": COLORS["accent"],
       "fontWeight": "bold", "fontSize": "11px",
       "border": _BDR, "padding": "4px 8px"}
_TD = {"backgroundColor": "#111111", "color": COLORS["text_light"],
       "border": _BDR, "fontSize": "12px",
       "padding": "4px 8px", "textAlign": "left",
       "fontFamily": "Consolas, monospace"}


# ── tiny helpers ──────────────────────────────────────────────────────────

def _ok(m):   return html.Span(m, style={"color": COLORS["success"], "fontSize": "12px"})
def _err(m):  return html.Span(m, style={"color": COLORS["danger"],  "fontSize": "12px"})
def _warn(m): return html.Span(m, style={"color": COLORS["warning"], "fontSize": "12px"})

def _sec(txt):
    return html.P(txt, style={"color": COLORS["text"], "letterSpacing": "2px",
                               "fontSize": "10px", "textTransform": "uppercase",
                               "marginBottom": "4px", "marginTop": "10px"})

# ID builders (one per strategy, totally fixed)
def _eid(s): return f"hdr-{s}"          # header div children
def _dt(s):  return f"dt-{s}"           # DataTable
def _fb(s):  return f"fb-{s}"           # feedback span


def _strat_card(name: str) -> dbc.Card:
    """Static skeleton — data filled by callbacks."""
    dflt = _DEF[name]
    params = dflt["params"]
    init_data = [{"param": k, "value": v, "original": v}
                 for k, v in params.items()]
    status = "ACTIF" if dflt["enabled"] else "INACTIF"
    sc = COLORS["success"] if dflt["enabled"] else COLORS["danger"]

    return dbc.Card(style={"marginBottom": "10px",
                            "border": _BDR, "borderRadius": "4px"},
                    children=[
        # ── header ─────────────────────────────────────────────────────
        dbc.CardHeader(
            id=_eid(name),
            style={"backgroundColor": "#0d0d0d", "padding": "8px 14px"},
            children=dbc.Row([
                dbc.Col(html.B(name,
                               style={"color": COLORS["accent"], "fontSize": "13px"}),
                        width="auto"),
                dbc.Col(dbc.Badge(status,
                                  id=f"badge-{name}",
                                  style={"backgroundColor": sc,
                                         "fontSize": "10px", "padding": "3px 7px"}),
                        width="auto"),
                dbc.Col(width=True),
                dbc.Col(html.Span(f"${dflt['capital']:.0f}",
                                  id=f"cap-disp-{name}",
                                  style={"color": COLORS["text_light"],
                                         "fontSize": "12px",
                                         "fontFamily": "Consolas, monospace"}),
                        width="auto"),
            ], className="g-2 align-items-center"),
        ),

        # ── body ───────────────────────────────────────────────────────
        dbc.CardBody(style={"backgroundColor": "#0a0a0a", "padding": "10px 14px"},
                     children=[

            # Action buttons
            dbc.Row([
                dbc.Col(dbc.Button("▶ Activer",   id=f"en-{name}",
                                   color="success",  size="sm", style=_BTN),  width="auto"),
                dbc.Col(dbc.Button("⏸ Désact.",   id=f"dis-{name}",
                                   color="secondary",size="sm", style=_BTN),  width="auto"),
                dbc.Col(dbc.Button("▶ Reprendre", id=f"res-{name}",
                                   color="warning",  size="sm", style=_BTN),  width="auto"),
                dbc.Col(dbc.Button("↺ Reset",     id=f"rst-{name}",
                                   color="info",     size="sm", style=_BTN),  width="auto"),
                dbc.Col(dbc.Button("⚡ Flatten",  id=f"flt-{name}",
                                   color="danger",   size="sm", style=_BTN),  width="auto"),
                dbc.Col(html.Div(id=_fb(name), style={"fontSize": "12px"}),
                        className="d-flex align-items-center"),
            ], className="g-1 align-items-center mb-2"),

            # Editable param DataTable
            dash_table.DataTable(
                id=_dt(name),
                columns=[
                    {"name": "Paramètre",      "id": "param",
                     "editable": False, "type": "text"},
                    {"name": "Valeur  ✎",      "id": "value",
                     "editable": True,  "type": "numeric"},
                    {"name": "Défaut",         "id": "original",
                     "editable": False, "type": "numeric"},
                ],
                data=init_data,
                editable=True,
                style_as_list_view=True,
                style_header=_TH,
                style_cell=_TD,
                style_data_conditional=[
                    {"if": {"state": "active"},
                     "backgroundColor": "#155a7a", "color": "#fff"},
                    {"if": {"column_id": "param"},
                     "color": COLORS["accent"], "fontWeight": "600"},
                    {"if": {"column_id": "original"},
                     "color": "#555", "fontStyle": "italic"},
                ],
                style_table={"marginBottom": "10px"},
            ),

            # Capital + Apply row
            dbc.Row([
                dbc.Col(html.Small("Capital USD :", style={"color": COLORS["text"],
                                                            "fontSize": "11px"}),
                        width="auto", className="d-flex align-items-center"),
                dbc.Col(dbc.Input(id=f"cap-in-{name}", type="number",
                                  min=0, step=10, placeholder="$",
                                  className="dark-input",
                                  style={"fontSize": "12px", "height": "30px",
                                         "width": "90px"}),
                        width="auto"),
                dbc.Col(dbc.Button("Set $", id=f"setcap-{name}",
                                   color="info", size="sm", style=_BTN),
                        width="auto"),
                dbc.Col(width=True),
                dbc.Col(dbc.Button("✓ Appliquer les paramètres",
                                   id=f"apply-{name}",
                                   color="success", size="sm",
                                   style={**_BTN, "fontWeight": "700"}),
                        width="auto"),
            ], className="g-2 align-items-center"),
        ]),
    ])


# ── layout ────────────────────────────────────────────────────────────────

def static_layout() -> html.Div:
    strat_opts = [{"label": s, "value": s} for s in _ALL]

    return html.Div(style={"maxWidth": "1000px"}, children=[

        # 1. Connection status
        html.Div(id="conn-status-bar",
                 style={"padding": "7px 14px", "borderRadius": "4px",
                        "border": _BDR, "marginBottom": "10px",
                        "backgroundColor": "#0d0d0d"}),

        # 2. Engine start / stop
        dbc.Row([
            dbc.Col(html.Small("MOTEUR", style={"color": COLORS["warning"],
                                                 "letterSpacing": "2px",
                                                 "fontWeight": "700",
                                                 "fontSize": "10px"}),
                    width="auto", className="d-flex align-items-center"),
            dbc.Col(dcc.Dropdown(
                id="engine-strat-select", options=strat_opts,
                value=["MomentumLS", "BreakoutControlled"],
                multi=True, placeholder="Stratégies...",
                className="dropdown-dark",
            ), width=5),
            dbc.Col(dbc.Button("▶ DÉMARRER", id="engine-start-btn",
                               color="success", size="sm", style=_BTN), width="auto"),
            dbc.Col(dbc.Button("⏹ ARRÊTER",  id="engine-stop-btn",
                               color="danger",  size="sm", style=_BTN), width="auto"),
            dbc.Col(html.Div(id="engine-cmd-result", style={"fontSize": "12px"}),
                    className="d-flex align-items-center"),
        ], className="g-2 align-items-center mb-2",
           style={"backgroundColor": "#0d0d0d", "padding": "8px 12px",
                  "border": f"2px solid {COLORS['warning']}",
                  "borderRadius": "4px", "marginBottom": "10px"}),

        # 3. Global controls
        dbc.Row([
            dbc.Col(html.Small("GLOBAL", style={"color": COLORS["text"],
                                                 "letterSpacing": "2px",
                                                 "fontWeight": "700",
                                                 "fontSize": "10px"}),
                    width="auto", className="d-flex align-items-center"),
            dbc.Col(dbc.Button("▶ ON",          id="g-btn-trading-on",
                               color="success",   size="sm", style=_BTN), width="auto"),
            dbc.Col(dbc.Button("⏸ OFF",         id="g-btn-trading-off",
                               color="secondary", size="sm", style=_BTN), width="auto"),
            dbc.Col(html.Div(style={"borderLeft": _BDR, "height": "26px"}), width="auto"),
            dbc.Col(dbc.Button("⚡ FLATTEN ALL", id="g-btn-flatten-all",
                               color="warning",   size="sm", style=_BTN), width="auto"),
            dbc.Col(dbc.Button("⏸ PAUSE 1h",    id="g-btn-pause-all",
                               color="secondary", size="sm", style=_BTN), width="auto"),
            dbc.Col(html.Div(id="global-cmd-result", style={"fontSize": "12px"}),
                    className="d-flex align-items-center"),
        ], className="g-2 align-items-center mb-3"),

        # 4. One card per strategy (always visible, params inline)
        _sec("Stratégies"),
        *[_strat_card(name) for name in _ALL],
    ])


# ── callbacks ─────────────────────────────────────────────────────────────

def register_callbacks(app) -> None:

    # ── Connection status ─────────────────────────────────────────────────

    @app.callback(
        Output("conn-status-bar", "children"),
        Output("conn-status-bar", "style"),
        Input("refresh-interval", "n_intervals"),
    )
    def _conn(_n):
        st  = _api.engine_status()
        pid = engine_ctrl.pid
        if st["connected"]:
            c, b = COLORS["success"], f"1px solid {COLORS['success']}"
            txt  = ("🟢  Connecté — " + st["exchange"]
                    + (f"  |  PID {pid}" if pid else "")
                    + f"  |  heartbeat {st['age_s']}s")
        elif st["running"]:
            c, b = COLORS["warning"], f"1px solid {COLORS['warning']}"
            txt  = f"🟡  En attente — {st['exchange']}  |  {st['age_s']}s"
        else:
            c, b = COLORS["danger"], f"1px solid {COLORS['danger']}"
            txt  = "🔴  Moteur non démarré — cliquer ▶ DÉMARRER"
        return (
            html.B(txt, style={"color": c, "fontSize": "12px"}),
            {"padding": "7px 14px", "borderRadius": "4px", "border": b,
             "backgroundColor": "#0d0d0d", "marginBottom": "10px"},
        )

    # ── Engine start / stop ───────────────────────────────────────────────

    @app.callback(
        Output("engine-cmd-result", "children"),
        Input("engine-start-btn", "n_clicks"),
        Input("engine-stop-btn",  "n_clicks"),
        State("engine-strat-select", "value"),
        prevent_initial_call=True,
    )
    def _engine(_a, _b, strategies):
        trig = dash.ctx.triggered_id
        if trig == "engine-start-btn":
            r = engine_ctrl.start(strategies=strategies or [], paper=True)
            if r["ok"]:
                return _ok(f"✓ PID {r['pid']}  ({', '.join(strategies or ['toutes'])})")
            return _err(f"✗ {r['error']}")
        r = engine_ctrl.stop()
        return _ok("✓ Arrêté.") if r["ok"] else _err(f"✗ {r['error']}")

    # ── Refresh ALL DataTables + badges (single callback, N outputs) ──────

    @app.callback(
        # 5× DataTable data
        *[Output(_dt(s), "data")          for s in _ALL],
        # 5× badge label + color
        *[Output(f"badge-{s}",   "children")               for s in _ALL],
        *[Output(f"badge-{s}",   "style")                  for s in _ALL],
        *[Output(f"cap-disp-{s}", "children")               for s in _ALL],
        Input("refresh-interval", "n_intervals"),
    )
    def _refresh_all(_n):
        live_list = load_strategy_status()
        live = {s.get("name"): s for s in live_list}
        now  = time.time()

        dt_data, badges, badge_styles, cap_disps = [], [], [], []

        for name in _ALL:
            s    = live.get(name, {})
            dflt = _DEF[name]

            # Params (live or fallback to defaults)
            raw = (s.get("params", {}) if s
                   else dflt["params"])
            params = {k: v for k, v in raw.items()
                      if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if not params:
                params = dflt["params"]
            dt_data.append([{"param": k, "value": v, "original": v}
                             for k, v in params.items()])

            # Status
            ena  = s.get("enabled", dflt["enabled"]) if s else dflt["enabled"]
            susp = float(s.get("suspended_until", 0) or 0)
            is_s = susp > now
            status = "SUSPENDU" if is_s else ("ACTIF" if ena else "INACTIF")
            sc = {"ACTIF": COLORS["success"],
                  "INACTIF": COLORS["danger"],
                  "SUSPENDU": COLORS["warning"]}.get(status, COLORS["text"])
            badges.append(status)
            badge_styles.append({"backgroundColor": sc, "fontSize": "10px",
                                  "padding": "3px 7px"})

            # Capital display
            cap = s.get("capital_allocated_usd", dflt["capital"])
            cap_disps.append(f"${cap:.0f}")

        return (*dt_data, *badges, *badge_styles, *cap_disps)

    # ── Action buttons (all 5 strategies × 5 actions = 25 inputs) ────────

    @app.callback(
        *[Output(_fb(s), "children") for s in _ALL],
        *[Input(f"en-{s}",  "n_clicks") for s in _ALL],
        *[Input(f"dis-{s}", "n_clicks") for s in _ALL],
        *[Input(f"res-{s}", "n_clicks") for s in _ALL],
        *[Input(f"rst-{s}", "n_clicks") for s in _ALL],
        *[Input(f"flt-{s}", "n_clicks") for s in _ALL],
        prevent_initial_call=True,
    )
    def _strat_actions(*_clicks):
        trig = dash.ctx.triggered_id
        if not trig:
            return [""] * len(_ALL)
        empty = [""] * len(_ALL)
        for prefix, label_fn, api_fn in [
            ("en-",  lambda n: f"▶ {n} activé",            _api.enable_strategy),
            ("dis-", lambda n: f"⏸ {n} désactivé",         _api.disable_strategy),
            ("res-", lambda n: f"▶ {n} suspension levée",  _api.reset_strategy),
            ("rst-", lambda n: f"↺ {n} streak remis à zéro", _api.reset_strategy),
            ("flt-", lambda n: f"⚡ {n} flatten",           _api.flatten_strategy),
        ]:
            if trig.startswith(prefix):
                name = trig[len(prefix):]
                try:
                    api_fn(name)
                except Exception as exc:
                    idx = _ALL.index(name)
                    empty[idx] = _err(f"✗ {exc}")
                    return empty
                idx = _ALL.index(name)
                empty[idx] = _ok(f"✓ {label_fn(name)} — ~5s")
                return empty
        return empty

    # ── Capital Set (5 buttons) ───────────────────────────────────────────

    @app.callback(
        *[Output(_fb(s), "children", allow_duplicate=True) for s in _ALL],
        *[Input(f"setcap-{s}", "n_clicks") for s in _ALL],
        *[State(f"cap-in-{s}", "value")    for s in _ALL],
        prevent_initial_call=True,
    )
    def _set_caps(*args):
        n = len(_ALL)
        _clicks, vals = args[:n], args[n:]
        trig = dash.ctx.triggered_id
        empty = [""] * n
        if not trig:
            return empty
        name = trig[len("setcap-"):]
        idx  = _ALL.index(name)
        try:
            val = float(vals[idx])
            assert val >= 0
        except (TypeError, ValueError, AssertionError):
            empty[idx] = _warn("⚠ Montant invalide")
            return empty
        _api.set_capital(name, val)
        empty[idx] = _ok(f"✓ Capital {name} → ${val:.0f} — ~5s")
        return empty

    # ── Apply params (5 buttons) ──────────────────────────────────────────

    @app.callback(
        *[Output(_fb(s), "children", allow_duplicate=True) for s in _ALL],
        *[Input(f"apply-{s}",  "n_clicks") for s in _ALL],
        *[State(_dt(s),        "data")     for s in _ALL],
        prevent_initial_call=True,
    )
    def _apply_params(*args):
        n = len(_ALL)
        _clicks, datas = args[:n], args[n:]
        trig = dash.ctx.triggered_id
        empty = [""] * n
        if not trig:
            return empty
        name = trig[len("apply-"):]
        idx  = _ALL.index(name)
        data = datas[idx]
        try:
            params = {row["param"]: float(row["value"])
                      for row in (data or []) if row.get("value") is not None}
            if not params:
                empty[idx] = _warn("⚠ Aucun paramètre")
                return empty
            _api.update_params(name, params)
        except Exception as exc:
            empty[idx] = _err(f"✗ {exc}")
            return empty
        changed = [r["param"] for r in (data or [])
                   if r.get("value") != r.get("original")]
        summary = ", ".join(changed) if changed else "aucune modification"
        empty[idx] = _ok(f"✓ {name} — {summary} — ~5s")
        return empty

    # ── Global commands ───────────────────────────────────────────────────

    @app.callback(
        Output("global-cmd-result", "children"),
        Input("g-btn-flatten-all",  "n_clicks"),
        Input("g-btn-pause-all",    "n_clicks"),
        Input("g-btn-trading-on",   "n_clicks"),
        Input("g-btn-trading-off",  "n_clicks"),
        prevent_initial_call=True,
    )
    def _global(_a, _b, _c, _d):
        trig = dash.ctx.triggered_id
        try:
            if   trig == "g-btn-flatten-all":  _api.flatten_all()
            elif trig == "g-btn-pause-all":    _api.pause_all(60)
            elif trig == "g-btn-trading-on":   _api.set_trading(True)
            elif trig == "g-btn-trading-off":  _api.set_trading(False)
        except Exception as exc:
            return _err(f"✗ {exc}")
        msgs = {"g-btn-flatten-all":  "⚡ FLATTEN ALL",
                "g-btn-pause-all":    "⏸ PAUSE 1h",
                "g-btn-trading-on":   "▶ TRADING ON",
                "g-btn-trading-off":  "⏸ TRADING OFF"}
        return _ok(f"✓ {msgs.get(trig, '?')} envoyé — ~5s")
