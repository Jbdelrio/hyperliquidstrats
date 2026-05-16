"""
strategies.py — Tab Stratégies (9 stratégies).

Chaque stratégie = carte avec badge statut, boutons contrôle,
DataTable éditable, champ capital, positions ouvertes.
IDs totalement fixes — zéro pattern-matching, zéro dynamic component.
"""
import time
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, dash_table, dcc, html

from gui.control_api import ControlAPI
from gui.data_loader import load_strategy_status, _cache, _json_cache
from gui.engine_controller import engine_ctrl
from gui.theme import COLORS, STRAT_COLORS

_REPO = Path(__file__).resolve().parents[2]

_RESET_FILES = [
    _REPO / "logs"       / "decisions_v9.csv",
    _REPO / "logs"       / "fills_v9.csv",
    _REPO / "logs"       / "engine_stdout.log",
    _REPO / "metrics_v9" / "metrics_v9.csv",
    _REPO / "runtime"    / "strategy_status.json",
    _REPO / "runtime"    / "calibration_data.json",
    _REPO / "runtime"    / "control.json",
    _REPO / "runtime"    / "control_result.json",
    _REPO / "runtime"    / "engine.pid",
    _REPO / "runtime"    / "engine_config.json",
]

_api = ControlAPI()

# All strategies in display order (Phase-1 original + Phase-2 new)
_ALL = [
    "S8EMS", "MomentumLS", "BreakoutControlled",
    "MeanReversionKalman", "FundingArbitrage",
    "DonchianTrend", "RSIBollingerReversion",
    "RotationMomentum", "RelativeValue",
    # Phase-2
    "SpotPerpBasis", "FundingCarryHedged",
    "OBImbalanceScalper", "VolatilityRegimeBreakout", "MetaAlpha",
]

_DEF = {
    "S8EMS": {"capital": 500, "enabled": True,
              "params": {"min_spread_bps": 8.0, "max_hold_s": 600,
                         "stop_loss_bps": 120, "max_leverage": 3,
                         "base_notional_pct": 0.20, "quote_refresh_s": 5.0,
                         "bouchaud_decay_s": 15, "wavelet_threshold": 6.0}},
    "MomentumLS": {"capital": 500, "enabled": True,
                   "params": {"rerank_seconds": 60, "top_k_long": 4,
                              "bottom_k_short": 4, "score_threshold": 8,
                              "stop_loss_pct": 3.0, "take_profit_pct": 3.0,
                              "trailing_stop_pct": 1.5, "max_hold_hours": 24}},
    "BreakoutControlled": {"capital": 500, "enabled": True,
                           "params": {"lookback_bars": 12, "bo_max_pct": 10.0,
                                      "vr_min": 0.8, "take_profit_pct": 4.0,
                                      "stop_below_resistance_pct": 2.0,
                                      "max_hold_hours": 20}},
    "MeanReversionKalman": {"capital": 500, "enabled": True,
                            "params": {"warmup_seconds": 60, "z_entry": 0.8,
                                       "z_exit": 0.0, "z_stop": 4.5,
                                       "vol_max_pct_per_min": 1.5,
                                       "max_hold_minutes": 240}},
    "FundingArbitrage": {"capital": 500, "enabled": True,
                         "params": {"funding_entry_threshold_pct_per_hour": 0.003,
                                    "funding_exit_threshold_pct_per_hour": 0.001,
                                    "stop_loss_pct": 4.0, "max_hold_cycles": 20}},
    # ── Nouvelles stratégies ───────────────────────────────────────────
    "DonchianTrend": {"capital": 500, "enabled": True,
                      "params": {"donchian_n": 36, "ema_1h_period": 50,
                                 "btc_regime_ema": 200, "vol_period": 20,
                                 "vol_multiplier": 1.1, "stop_loss_pct": 0.010,
                                 "take_profit_pct": 0.020, "min_cost_ratio": 2.5,
                                 "max_hold_hours": 36}},
    "RSIBollingerReversion": {"capital": 500, "enabled": True,
                              "params": {"rsi_period": 14, "rsi_oversold": 35,
                                         "zscore_period": 30, "zscore_entry": -1.5,
                                         "bb_period": 20, "bb_k": 2.0,
                                         "ema_1h_period": 100, "stop_loss_pct": 0.008,
                                         "take_profit_pct": 0.015,
                                         "time_stop_bars": 16, "min_cost_ratio": 2.5}},
    "RotationMomentum": {"capital": 0, "enabled": True,
                         "params": {"momentum_lookback": 24, "top_k": 3,
                                    "bottom_k": 3, "min_momentum": 0.0,
                                    "rebalance_minutes": 60, "autonomous": 0,
                                    "stop_loss_pct": 0.04, "take_profit_pct": 0.020,
                                    "max_hold_hours": 12, "min_cost_ratio": 2.5}},
    "RelativeValue": {"capital": 500, "enabled": False,
                      "params": {"regression_lookback": 500, "zscore_lookback": 200,
                                 "entry_z": -2.0, "exit_z": 0.0, "stop_z": -3.5,
                                 "min_correlation": 0.70, "stop_loss_pct": 0.05,
                                 "take_profit_pct": 0.03, "max_hold_hours": 48,
                                 "min_cost_ratio": 2.5}},
    # Phase-2 strategies (disabled by default)
    "SpotPerpBasis": {"capital": 500, "enabled": False,
                      "params": {"basis_entry_bps": 20.0, "basis_exit_bps": 5.0,
                                 "max_basis_abs_bps": 200.0, "min_expected_edge_bps": 8.0,
                                 "stop_loss_pct": 0.015, "take_profit_pct": 0.010,
                                 "max_hold_minutes": 240,
                                 "trade_when_external_spot_missing": False,
                                 "external_spot_prices": {}}},
    "FundingCarryHedged": {"capital": 500, "enabled": False,
                           "params": {"funding_entry_bps_per_hour": 0.5,
                                      "funding_exit_bps_per_hour": 0.1,
                                      "taker_fee_bps": 3.5, "slippage_bps": 2.0,
                                      "safety_buffer_bps": 2.0, "min_expected_edge_bps": 3.0,
                                      "max_abs_return_15m_pct": 2.5, "stop_loss_pct": 0.02,
                                      "take_profit_pct": 0.012, "max_hold_hours": 8,
                                      "allow_unhedged_perp": False}},
    "OBImbalanceScalper": {"capital": 300, "enabled": False,
                           "params": {"imbalance_entry_threshold": 0.30,
                                      "imbalance_exit_threshold": 0.05,
                                      "imbalance_levels": 5,
                                      "min_persistence_updates": 3,
                                      "stop_loss_pct": 0.004, "take_profit_pct": 0.003,
                                      "max_hold_seconds": 120}},
    "VolatilityRegimeBreakout": {"capital": 500, "enabled": False,
                                 "params": {"donchian_period": 20, "atr_period": 14,
                                            "high_vol_threshold_bps": 30.0,
                                            "low_vol_threshold_bps": 8.0,
                                            "stop_loss_pct": 0.015, "take_profit_pct": 0.025,
                                            "max_hold_hours": 4}},
    "MetaAlpha": {"capital": 500, "enabled": False,
                  "params": {"min_agreement_score": 2, "stop_loss_pct": 0.012,
                             "take_profit_pct": 0.018, "max_hold_hours": 6}},
}

_BTN = {"fontWeight": "700", "fontSize": "11px"}
_BDR = f"1px solid {COLORS['grid']}"

_TH = {"backgroundColor": "#060606", "color": COLORS["accent"],
       "fontWeight": "bold", "fontSize": "11px", "border": _BDR, "padding": "4px 8px"}
_TD_DT = {"backgroundColor": "#111111", "color": COLORS["text_light"],
          "border": _BDR, "fontSize": "12px", "padding": "4px 8px",
          "textAlign": "left", "fontFamily": "Consolas, monospace"}

_TH_POS = {"backgroundColor": "#060606", "color": COLORS["accent"],
           "fontWeight": "bold", "fontSize": "10px",
           "border": _BDR, "padding": "3px 6px", "letterSpacing": "1px",
           "textTransform": "uppercase"}
_TD_POS = {"border": _BDR, "padding": "3px 6px", "fontSize": "11px",
           "fontFamily": "Consolas, monospace", "color": COLORS["text_light"]}


def _ok(m):   return html.Span(m, style={"color": COLORS["success"], "fontSize": "12px"})
def _err(m):  return html.Span(m, style={"color": COLORS["danger"],  "fontSize": "12px"})
def _warn(m): return html.Span(m, style={"color": COLORS["warning"], "fontSize": "12px"})

def _sec(txt):
    return html.P(txt, style={"color": COLORS["text"], "letterSpacing": "2px",
                               "fontSize": "10px", "textTransform": "uppercase",
                               "marginBottom": "4px", "marginTop": "10px"})

def _grp_hdr(txt, color=None):
    return html.P(txt, style={"color": color or COLORS["accent"], "fontSize": "9px",
                               "letterSpacing": "2px", "textTransform": "uppercase",
                               "fontWeight": "700", "marginTop": "14px",
                               "marginBottom": "4px",
                               "borderBottom": f"1px solid {COLORS['grid']}",
                               "paddingBottom": "4px"})

# ID builders — one per strategy, totally fixed
def _dt(s):  return f"dt-{s}"
def _fb(s):  return f"fb-{s}"
def _pd(s):  return f"pos-div-{s}"


def _render_positions(name: str, positions: list) -> html.Div:
    header = _sec("Positions ouvertes")
    if not positions:
        return html.Div([
            header,
            html.Small("— aucune position —",
                       style={"color": "#444", "fontSize": "11px",
                              "paddingLeft": "4px", "fontStyle": "italic"}),
        ])
    rows = []
    for p in positions:
        upnl = p.get("unrealized_pnl", 0.0)
        uc   = COLORS["success"] if upnl >= 0 else COLORS["danger"]
        sc   = COLORS["success"] if p["side"] == "BUY" else COLORS["danger"]
        tp   = p.get("tp_price") or "—"
        sl   = p.get("stop_price") or "—"
        tp_s = f"{tp:.5g}" if isinstance(tp, float) else tp
        sl_s = f"{sl:.5g}" if isinstance(sl, float) else sl
        rows.append(html.Tr([
            html.Td(p["symbol"], style={**_TD_POS, "color": COLORS["accent"], "fontWeight": "700"}),
            html.Td(p["side"],   style={**_TD_POS, "color": sc, "fontWeight": "700"}),
            html.Td(f"${p['notional_usd']:.0f}", style=_TD_POS),
            html.Td(f"{p['entry_price']:.5g}",   style=_TD_POS),
            html.Td(f"{p.get('current_price', p['entry_price']):.5g}", style=_TD_POS),
            html.Td(f"${upnl:+.4f}", style={**_TD_POS, "color": uc, "fontWeight": "700"}),
            html.Td(f"{p['hold_s']}s", style=_TD_POS),
            html.Td(f"TP {tp_s}", style={**_TD_POS, "color": "#888"}),
            html.Td(f"SL {sl_s}", style={**_TD_POS, "color": "#888"}),
            html.Td(
                dbc.Button("✕", size="sm", color="danger",
                           id={"type": "close-pos", "index": f"{name}|{p['pos_id']}"},
                           style={"padding": "0 5px", "fontSize": "10px",
                                  "lineHeight": "1.4"}),
                style={**_TD_POS, "padding": "2px 4px"},
            ),
        ]))
    thead = html.Thead(html.Tr([
        html.Th(h, style=_TH_POS)
        for h in ["COIN", "SIDE", "NOTIO", "ENTRY", "MID", "PNL", "HOLD", "TP", "SL", ""]
    ]))
    return html.Div([
        header,
        html.Table([thead, html.Tbody(rows)],
                   style={"width": "100%", "borderCollapse": "collapse",
                           "marginBottom": "4px"}),
    ])


def _render_ledger(ldg: dict) -> html.Div:
    if not ldg:
        return html.Div()

    state = ldg.get("state", "unknown")
    state_color = {
        "active":    COLORS["success"],
        "suspended": COLORS["warning"],
        "killed":    COLORS["danger"],
        "disabled":  "#555",
    }.get(state, COLORS["text"])

    def _fmt_usd(v):
        if v is None: return "—"
        return f"${v:+.2f}" if v != 0 else "$0.00"

    def _cell(label, value, color=None):
        return html.Div([
            html.Span(label, style={"color": "#555", "fontSize": "10px",
                                    "letterSpacing": "1px", "textTransform": "uppercase"}),
            html.Span(value, style={"color": color or COLORS["text_light"],
                                    "fontSize": "11px", "fontFamily": "Consolas,monospace",
                                    "marginLeft": "4px", "fontWeight": "600"}),
        ], style={"display": "inline-block", "marginRight": "12px", "marginBottom": "2px"})

    eq    = ldg.get("equity")
    rpnl  = ldg.get("realized_pnl", 0.0)
    upnl  = ldg.get("unrealized_pnl", 0.0)
    avail = ldg.get("available_capital")
    dd    = ldg.get("drawdown_pct")
    init  = ldg.get("initial_capital_usd")
    onot  = ldg.get("open_notional", 0.0)
    rnot  = ldg.get("reserved_notional", 0.0)
    pend  = ldg.get("pending_orders_count", 0)
    susp  = ldg.get("suspended_until_readable", "")

    rpnl_c = COLORS["success"] if (rpnl or 0) >= 0 else COLORS["danger"]
    upnl_c = COLORS["success"] if (upnl or 0) >= 0 else COLORS["danger"]
    dd_c   = COLORS["danger"] if (dd or 0) > 3 else COLORS["warning"] if (dd or 0) > 1 else "#888"

    state_badge = dbc.Badge(
        state.upper(), color=None,
        style={"backgroundColor": state_color, "fontSize": "9px",
               "padding": "2px 6px", "marginRight": "8px", "verticalAlign": "middle"},
    )

    susp_note = (html.Span(f"(jusqu'à {susp})", style={"color": COLORS["warning"],
                 "fontSize": "10px", "marginLeft": "4px"})
                 if susp else None)

    row1 = html.Div([
        state_badge,
        *([] if susp_note is None else [susp_note]),
        _cell("Capital", f"${init:.0f}" if init is not None else "—"),
        _cell("Equity",  f"${eq:.2f}"   if eq  is not None else "—"),
        _cell("Avail",   f"${avail:.2f}" if avail is not None else "—"),
        _cell("DD",      f"{dd:.2f}%"    if dd   is not None else "—", dd_c),
    ], style={"marginBottom": "2px"})

    row2 = html.Div([
        _cell("Real PnL",  _fmt_usd(rpnl), rpnl_c),
        _cell("Unreal PnL", _fmt_usd(upnl), upnl_c),
        _cell("Open Not.", f"${onot:.2f}"),
        _cell("Res. Not.", f"${rnot:.2f}"),
        _cell("Pending",   str(pend),
              COLORS["warning"] if pend else COLORS["text_light"]),
    ])

    return html.Div([row1, row2],
                    style={"backgroundColor": "#080808",
                           "border": f"1px solid {COLORS['grid']}",
                           "borderRadius": "3px", "padding": "5px 8px",
                           "marginTop": "4px"})


def _strat_card(name: str) -> dbc.Card:
    dflt      = _DEF[name]
    params    = dflt["params"]
    init_data = [{"param": k, "value": v, "original": v}
                 for k, v in params.items()]
    status    = "ACTIF" if dflt["enabled"] else "INACTIF"
    sc        = COLORS["success"] if dflt["enabled"] else COLORS["danger"]
    accent    = STRAT_COLORS.get(name, COLORS["accent"])

    # Special label for paper/scanner strategies
    extra = ""
    if name == "RelativeValue":
        extra = " [PAPER ONLY]"
    elif name == "RotationMomentum":
        extra = " [SCANNER]"

    return dbc.Card(
        style={"marginBottom": "8px", "border": _BDR, "borderRadius": "4px",
               "borderLeft": f"3px solid {accent}"},
        className="card-glow",
        children=[
            dbc.CardHeader(
                style={"backgroundColor": "#0d0d0d", "padding": "7px 14px"},
                children=dbc.Row([
                    dbc.Col(html.B(f"{name}{extra}",
                                   style={"color": accent, "fontSize": "12px"}),
                            width="auto"),
                    dbc.Col(dbc.Badge(status, id=f"badge-{name}",
                                      style={"backgroundColor": sc,
                                             "fontSize": "9px", "padding": "2px 6px"}),
                            width="auto"),
                    dbc.Col(width=True),
                    dbc.Col(html.Span(f"${dflt['capital']:.0f}", id=f"cap-disp-{name}",
                                      style={"color": COLORS["text_light"],
                                             "fontSize": "11px",
                                             "fontFamily": "Consolas, monospace"}),
                            width="auto"),
                ], className="g-2 align-items-center"),
            ),
            dbc.CardBody(
                style={"backgroundColor": "#0a0a0a", "padding": "9px 14px"},
                children=[
                    dbc.Row([
                        dbc.Col(dbc.Button("▶ Activer",    id=f"en-{name}",  color="success",
                                           size="sm", style=_BTN), width="auto"),
                        dbc.Col(dbc.Button("⏸ Seulement",  id=f"dis-{name}", color="secondary",
                                           size="sm", style=_BTN,
                                           title="Disable only — garde les positions ouvertes"),
                                width="auto"),
                        dbc.Col(dbc.Button("⏸+✗ Cancel",   id=f"dco-{name}", color="warning",
                                           size="sm", style=_BTN,
                                           title="Disable + annuler les ordres en attente"),
                                width="auto"),
                        dbc.Col(dbc.Button("⏸+⚡ Flatten",  id=f"dfl-{name}", color="danger",
                                           size="sm", style=_BTN,
                                           title="Disable + cancel + fermer les positions"),
                                width="auto"),
                        dbc.Col(dbc.Button("▶ Reprendre",  id=f"res-{name}", color="info",
                                           size="sm", style=_BTN), width="auto"),
                        dbc.Col(dbc.Button("↺ Reset",      id=f"rst-{name}", color="info",
                                           size="sm", style=_BTN), width="auto"),
                        dbc.Col(dbc.Button("⚡ Flatten",   id=f"flt-{name}", color="danger",
                                           size="sm", style=_BTN), width="auto"),
                        dbc.Col(html.Div(id=_fb(name), style={"fontSize": "12px"}),
                                className="d-flex align-items-center"),
                    ], className="g-1 align-items-center mb-2"),

                    dash_table.DataTable(
                        id=_dt(name),
                        columns=[
                            {"name": "Paramètre", "id": "param",
                             "editable": False, "type": "text"},
                            {"name": "Valeur  ✎", "id": "value",
                             "editable": True,  "type": "numeric"},
                            {"name": "Défaut",    "id": "original",
                             "editable": False, "type": "numeric"},
                        ],
                        data=init_data,
                        editable=True,
                        style_as_list_view=True,
                        style_header=_TH,
                        style_cell=_TD_DT,
                        style_data_conditional=[
                            {"if": {"state": "active"},
                             "backgroundColor": "#155a7a", "color": "#fff"},
                            {"if": {"column_id": "param"},
                             "color": accent, "fontWeight": "600"},
                            {"if": {"column_id": "original"},
                             "color": "#555", "fontStyle": "italic"},
                        ],
                        style_table={"marginBottom": "8px"},
                    ),

                    dbc.Row([
                        dbc.Col(html.Small("Capital USD :",
                                           style={"color": COLORS["text"], "fontSize": "11px"}),
                                width="auto", className="d-flex align-items-center"),
                        dbc.Col(dbc.Input(id=f"cap-in-{name}", type="number",
                                          min=0, step=10, placeholder="$",
                                          className="dark-input",
                                          style={"fontSize": "12px", "height": "28px",
                                                 "width": "80px"}),
                                width="auto"),
                        dbc.Col(dbc.Button("Set $", id=f"setcap-{name}",
                                           color="info", size="sm", style=_BTN),
                                width="auto"),
                        dbc.Col(width=True),
                        dbc.Col(dbc.Button("✓ Appliquer", id=f"apply-{name}",
                                           color="success", size="sm",
                                           style={**_BTN, "fontWeight": "700"}),
                                width="auto"),
                    ], className="g-2 align-items-center"),

                    html.Div(id=_pd(name), style={"marginTop": "6px"}),
                    html.Div(id=f"ldg-{name}", style={"marginTop": "4px"}),
                ],
            ),
        ],
    )


# ── Layout ────────────────────────────────────────────────────────────────

def _step(n, text):
    return html.Div(className="guide-step", children=[
        html.Span(str(n), className="guide-step-num"),
        html.Div(className="guide-step-text", children=text),
    ])

def _cmd(s):
    return html.Code(s, className="guide-cmd")


def _fresh_start_guide() -> html.Div:
    return html.Div([
        _step(1, ["Cloner et installer: ",
                  _cmd("pip install -r requirements.txt")]),
        _step(2, ["Copier la config: ",
                  _cmd("cp .env.example .env"),
                  html.Span("  puis éditer ", style={"color": COLORS["text"]}),
                  _cmd(".env"),
                  html.Span(" (ajouter clés API si LLM activé)", style={"color": COLORS["text"]})]),
        _step(3, ["Démarrage vierge (efface anciens logs): ",
                  _cmd("python -m gui.app --fresh"),
                  html.Span("  puis ouvrir ", style={"color": COLORS["text"]}),
                  _cmd("http://127.0.0.1:8050")]),
        _step(4, ["Sélectionner les stratégies souhaitées dans le dropdown moteur "
                  "et cliquer ", html.B("▶ DÉMARRER", style={"color": COLORS["success"]}),
                  html.Span(". Le moteur tourne en mode paper par défaut — aucun ordre réel.",
                            style={"color": COLORS["warning"], "marginLeft": "6px"})]),
        _step(5, ["Observer l'onglet ", html.B("Overview", style={"color": COLORS["accent"]}),
                  " pour le PnL en temps réel, ",
                  html.B("Calibration", style={"color": COLORS["accent"]}),
                  " pour les signaux raw de chaque stratégie."]),
        _step(6, [html.B("Nouvelles stratégies Phase 2 ", style={"color": "#00D4AA"}),
                  "sont toutes désactivées par défaut. Activer une par une après validation paper. "
                  "FundingCarryHedged et SpotPerpBasis sont en mode scanner (pas de trades) "
                  "jusqu'à ce que ", _cmd("allow_unhedged_perp: true"),
                  " / ", _cmd("external_spot_prices"),
                  " soient configurés."]),
        _step(7, [html.B("LLM Overlay ", style={"color": COLORS["accent"]}),
                  ": désactivé par défaut. Activer avec ",
                  _cmd("LLM_ENABLED=true"), " dans ", _cmd(".env"),
                  " + clé API OpenAI/compatible. Vérifier le Brier Score dans l'onglet ",
                  html.B("LLM Overlay", style={"color": COLORS["accent"]}),
                  " avant de faire confiance aux signaux."]),
    ], style={"marginBottom": "24px"})


def static_layout() -> html.Div:
    strat_opts = [{"label": s, "value": s} for s in _ALL]

    reset_modal = dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle("RESET COMPLET — Confirmer ?",
                                        style={"color": COLORS["danger"],
                                               "fontSize": "14px", "fontWeight": "700"})),
        dbc.ModalBody([
            html.P("Cette action va :", style={"color": COLORS["text"], "fontSize": "12px"}),
            html.Ul([
                html.Li("Arrêter le moteur si actif"),
                html.Li("Supprimer tous les trades, décisions et métriques"),
                html.Li("Remettre à zéro l'état de toutes les stratégies"),
                html.Li("Effacer les fichiers runtime"),
            ], style={"color": COLORS["warning"], "fontSize": "12px"}),
            html.P("Cette action est irréversible.", style={"color": COLORS["danger"],
                                                             "fontWeight": "700",
                                                             "fontSize": "12px",
                                                             "marginTop": "8px"}),
        ]),
        dbc.ModalFooter([
            dbc.Button("✗ Annuler",       id="reset-cancel-btn",  color="secondary",
                       size="sm", className="me-2"),
            dbc.Button("✓ CONFIRMER RESET", id="reset-confirm-btn", color="danger",
                       size="sm"),
        ]),
    ], id="reset-confirm-modal", is_open=False, centered=True)

    return html.Div(style={"maxWidth": "1020px"}, children=[

        reset_modal,

        # Capital overrides stored in browser session (survives auto-refresh)
        dcc.Store(id="cap-store", storage_type="session", data={}),

        # Connection status (local, detailed)
        html.Div(id="conn-status-bar",
                 style={"padding": "7px 14px", "borderRadius": "4px",
                        "border": _BDR, "marginBottom": "10px",
                        "backgroundColor": "#0d0d0d"}),

        # ── Engine start / stop ────────────────────────────────────────
        html.Div(style={"backgroundColor": "#0d0d0d", "padding": "10px 14px",
                        "border": f"2px solid {COLORS['warning']}",
                        "borderRadius": "4px", "marginBottom": "10px"}, children=[

            # Row 1: label + strat select + tout/aucun
            dbc.Row([
                dbc.Col(html.Small("MOTEUR", style={"color": COLORS["warning"],
                                                     "letterSpacing": "2px",
                                                     "fontWeight": "900", "fontSize": "10px"}),
                        width="auto", className="d-flex align-items-center"),
                dbc.Col(dcc.Dropdown(
                    id="engine-strat-select", options=strat_opts,
                    value=[],
                    multi=True, placeholder="Toutes (selon config)...",
                    className="dropdown-dark",
                ), width=7),
                dbc.Col(dbc.Button("☑ Tout",   id="engine-select-all-btn",
                                   color="secondary", size="sm", style=_BTN), width="auto"),
                dbc.Col(dbc.Button("☐ Aucun",  id="engine-select-none-btn",
                                   color="secondary", size="sm", style=_BTN), width="auto"),
            ], className="g-2 align-items-center mb-2"),

            # Row 2: preset config
            dbc.Row([
                dbc.Col(html.Small("PRESET", style={"color": COLORS["warning"],
                                                     "letterSpacing": "1px",
                                                     "fontWeight": "700", "fontSize": "10px"}),
                        width="auto", className="d-flex align-items-center"),
                dbc.Col(dcc.Dropdown(
                    id="engine-config-select",
                    options=[
                        {"label": "★ Paper 500 IDEAL — 5 strats + alpha research + funding scan (RECOMMENDED)",
                         "value": "config/presets/paper_500_ideal.json"},
                        {"label": "Alpha Research only (collecte seconds features)",
                         "value": "config/presets/paper_500_alpha_research.json"},
                        {"label": "Funding Research only (scan HL + Aster)",
                         "value": "config/presets/paper_500_funding_research.json"},
                        {"label": "Paper 500 Clean — 5 strats × 500 USD",
                         "value": "config/presets/paper_500_clean.json"},
                        {"label": "Paper 500 Per Strategy — 5 actives + 9 inactives",
                         "value": "config/presets/paper_500_per_strategy.json"},
                        {"label": "Default (config_v9.json)",
                         "value": "config_v9.json"},
                    ],
                    value="config/presets/paper_500_ideal.json",
                    clearable=False,
                    className="dropdown-dark",
                ), width=5),
            ], className="g-2 align-items-center mb-2"),

            # Row 3: exchange + LLM + start + stop
            dbc.Row([
                dbc.Col(html.Small("EXCHANGE", style={"color": COLORS["text"],
                                                       "letterSpacing": "1px",
                                                       "fontWeight": "700", "fontSize": "10px"}),
                        width="auto", className="d-flex align-items-center"),
                dbc.Col(dcc.Dropdown(
                    id="engine-exchange-select",
                    options=[
                        {"label": "Hyperliquid (WebSocket)", "value": "hyperliquid"},
                        {"label": "Binance USDT-M (REST)",   "value": "binance"},
                        {"label": "Bitget USDT-M (REST)",    "value": "bitget"},
                    ],
                    value="hyperliquid", clearable=False,
                    className="dropdown-dark",
                ), width=3),
                dbc.Col(html.Div(style={"borderLeft": _BDR, "height": "26px"}), width="auto"),
                dbc.Col(html.Small("LLM", style={"color": COLORS["accent"],
                                                  "letterSpacing": "1px",
                                                  "fontWeight": "700", "fontSize": "10px"}),
                        width="auto", className="d-flex align-items-center"),
                dbc.Col(dbc.Button("LLM ON",  id="llm-on-btn",
                                   color="info",      size="sm", style=_BTN), width="auto"),
                dbc.Col(dbc.Button("LLM OFF", id="llm-off-btn",
                                   color="secondary", size="sm", style=_BTN), width="auto"),
                dbc.Col(html.Div(id="llm-status-indicator",
                                 style={"fontSize": "11px", "color": COLORS["text"]}),
                        className="d-flex align-items-center"),
                dbc.Col(html.Div(style={"borderLeft": _BDR, "height": "26px"}), width="auto"),
                dbc.Col(dbc.Button("▶ DÉMARRER", id="engine-start-btn",
                                   color="success", size="sm",
                                   style={**_BTN, "letterSpacing": "1px"}), width="auto"),
                dbc.Col(dbc.Button("⏹ ARRÊTER",  id="engine-stop-btn",
                                   color="danger",  size="sm", style=_BTN), width="auto"),
                dbc.Col(html.Div(id="engine-cmd-result", style={"fontSize": "12px"}),
                        className="d-flex align-items-center"),
            ], className="g-2 align-items-center"),
        ]),

        # ── Global controls ────────────────────────────────────────────
        dbc.Row([
            dbc.Col(html.Small("GLOBAL", style={"color": COLORS["text"],
                                                 "letterSpacing": "2px",
                                                 "fontWeight": "700", "fontSize": "10px"}),
                    width="auto", className="d-flex align-items-center"),
            dbc.Col(dbc.Button("▶ ON",         id="g-btn-trading-on",
                               color="success",   size="sm", style=_BTN), width="auto"),
            dbc.Col(dbc.Button("⏸ OFF",        id="g-btn-trading-off",
                               color="secondary", size="sm", style=_BTN), width="auto"),
            dbc.Col(html.Div(style={"borderLeft": _BDR, "height": "26px"}), width="auto"),
            dbc.Col(dbc.Button("⚡ FLATTEN ALL", id="g-btn-flatten-all",
                               color="warning",   size="sm", style=_BTN), width="auto"),
            dbc.Col(dbc.Button("⏸ PAUSE 1h",  id="g-btn-pause-all",
                               color="secondary", size="sm", style=_BTN), width="auto"),
            dbc.Col(html.Div(style={"borderLeft": _BDR, "height": "26px"}), width="auto"),
            dbc.Col(dbc.Button("RESET TOUT", id="reset-all-btn",
                               color="danger",    size="sm",
                               style={**_BTN, "letterSpacing": "1px"}), width="auto"),
            dbc.Col(html.Div(id="global-cmd-result", style={"fontSize": "12px"}),
                    className="d-flex align-items-center"),
        ], className="g-2 align-items-center mb-3"),

        # ── LLM mode toggle (Phase-6) ─────────────────────────────────
        dbc.Row([
            dbc.Col(html.Small("LLM MODE", style={"color": COLORS["accent"],
                                                   "letterSpacing": "2px",
                                                   "fontWeight": "700",
                                                   "fontSize": "10px"}),
                    width="auto", className="d-flex align-items-center"),
            dbc.Col(dbc.Button("OFF",      id="llm-mode-off-btn",
                               color="secondary", size="sm", style=_BTN), width="auto"),
            dbc.Col(dbc.Button("OBSERVER", id="llm-mode-observer-btn",
                               color="info",   size="sm", style=_BTN), width="auto"),
            dbc.Col(dbc.Button("RISK_OVERLAY", id="llm-mode-risk-btn",
                               color="warning", size="sm", style=_BTN), width="auto"),
            dbc.Col(html.Div(id="llm-mode-result",
                             style={"fontSize": "11px",
                                    "color": COLORS["text"]}),
                    className="d-flex align-items-center"),
        ], className="g-2 align-items-center mb-3"),

        # ── Quick strategy toggles ─────────────────────────────────────
        html.Div(style={"backgroundColor": "#0a0a0a", "padding": "8px 14px",
                        "border": _BDR, "borderRadius": "4px", "marginBottom": "10px"},
                 children=[
            html.Small("TOGGLES RAPIDES — activer / désactiver une stratégie en un clic",
                       style={"color": COLORS["text"], "letterSpacing": "1px",
                              "fontSize": "9px", "fontWeight": "700",
                              "textTransform": "uppercase"}),
            html.Div(id="strat-quick-toggles", style={"marginTop": "6px"}),
        ]),

        # ── Engine log (last lines) ────────────────────────────────────
        html.Details([
            html.Summary(html.Small("LOG MOTEUR — dernières lignes",
                                    style={"color": COLORS["text"], "letterSpacing": "1px",
                                           "fontSize": "10px", "fontWeight": "700",
                                           "textTransform": "uppercase", "cursor": "pointer"})),
            html.Div(id="engine-log-box",
                     style={"backgroundColor": "#060606", "border": f"1px solid {COLORS['grid']}",
                            "borderRadius": "3px", "padding": "8px 12px",
                            "fontFamily": "Consolas,monospace", "fontSize": "11px",
                            "color": "#ADAFAE", "maxHeight": "180px",
                            "overflowY": "auto", "whiteSpace": "pre-wrap",
                            "marginTop": "6px"}),
        ], style={"marginBottom": "10px"}),

        # ── Strategy cards ─────────────────────────────────────────────
        _grp_hdr("Stratégies existantes"),
        *[_strat_card(n) for n in
          ["S8EMS", "MomentumLS", "BreakoutControlled",
           "MeanReversionKalman", "FundingArbitrage"]],

        _grp_hdr("Nouvelles stratégies", COLORS["success"]),
        *[_strat_card(n) for n in
          ["DonchianTrend", "RSIBollingerReversion"]],

        _grp_hdr("Scanner / Expérimental", COLORS["warning"]),
        *[_strat_card(n) for n in ["RotationMomentum", "RelativeValue"]],

        _grp_hdr("Phase 2 — Nouvelles stratégies", "#00D4AA"),
        *[_strat_card(n) for n in
          ["SpotPerpBasis", "FundingCarryHedged",
           "OBImbalanceScalper", "VolatilityRegimeBreakout", "MetaAlpha"]],

        # ── Fresh start guide ──────────────────────────────────────────
        html.Hr(style={"borderColor": COLORS["grid"], "marginTop": "24px"}),
        html.P("GUIDE DÉMARRAGE VIERGE", style={"color": COLORS["accent"],
               "fontSize": "10px", "letterSpacing": "2px", "fontWeight": "700",
               "textTransform": "uppercase", "marginBottom": "10px"}),
        _fresh_start_guide(),
    ])


# ── Callbacks ─────────────────────────────────────────────────────────────

def register_callbacks(app) -> None:

    # ── Connection status (detailed, with PID) ────────────────────────────

    @app.callback(
        Output("conn-status-bar", "children"),
        Output("conn-status-bar", "style"),
        Input("refresh-interval", "n_intervals"),
    )
    def _conn(_n):
        st  = _api.engine_status()
        pid = engine_ctrl.pid
        if st["connected"]:
            c, b = COLORS["success"], f"1px solid {COLORS['success']}55"
            txt  = ("Connecté — Hyperliquid"
                    + (f"  |  PID {pid}" if pid else "")
                    + f"  |  heartbeat {st['age_s']}s")
            dot = html.Span(className="conn-dot-live")
        elif st.get("starting"):
            c, b = COLORS["warning"], f"1px solid {COLORS['warning']}55"
            txt  = f"En démarrage… connexion WebSocket  |  {st['age_s']}s"
            dot = html.Span(className="conn-dot-warn")
        elif st["running"]:
            c, b = COLORS["warning"], f"1px solid {COLORS['warning']}55"
            txt  = f"En attente — Hyperliquid  |  {st['age_s']}s"
            dot = html.Span(className="conn-dot-warn")
        else:
            c, b = COLORS["danger"], f"1px solid {COLORS['danger']}55"
            txt  = "Moteur non démarré — cliquer ▶ DÉMARRER"
            dot = html.Span(className="conn-dot-dead")
        return (
            html.Span([dot, html.B(txt, style={"color": c, "fontSize": "12px"})]),
            {"padding": "7px 14px", "borderRadius": "4px", "border": b,
             "backgroundColor": "#0d0d0d", "marginBottom": "10px",
             "display": "flex", "alignItems": "center", "gap": "8px"},
        )

    # ── Select all / none / preset auto-clear ────────────────────────────────

    @app.callback(
        Output("engine-strat-select", "value"),
        Input("engine-select-all-btn",  "n_clicks"),
        Input("engine-select-none-btn", "n_clicks"),
        Input("engine-config-select",   "value"),
        prevent_initial_call=True,
    )
    def _select_strats(_all, _none, config_val):
        trig = dash.ctx.triggered_id
        if trig == "engine-select-all-btn":
            return _ALL
        if trig == "engine-select-none-btn":
            return []
        # When any config is chosen, clear the dropdown — the config controls
        # which strategies are enabled; the dropdown is only for manual overrides
        if trig == "engine-config-select":
            return []
        return dash.no_update

    # ── Engine start / stop ───────────────────────────────────────────────

    @app.callback(
        Output("engine-cmd-result", "children"),
        Input("engine-start-btn", "n_clicks"),
        Input("engine-stop-btn",  "n_clicks"),
        State("engine-strat-select",   "value"),
        State("engine-exchange-select", "value"),
        State("engine-config-select",   "value"),
        prevent_initial_call=True,
    )
    def _engine(_a, _b, strats, exchange, config_path):
        trig = dash.ctx.triggered_id
        if trig == "engine-start-btn":
            cfg = config_path or "config_v9.json"
            # Empty dropdown = let the config decide which strategies are enabled.
            # Populated dropdown = explicit override via --strategy flag.
            strat_override = strats or []
            r = engine_ctrl.start(
                strategies=strat_override, paper=True,
                exchange=exchange or "hyperliquid",
                config=cfg,
            )
            cfg_short = cfg.split("/")[-1]
            strat_note = f" strats={strat_override}" if strat_override else ""
            return (_ok(f"✓ PID {r['pid']} [{exchange}] [{cfg_short}]{strat_note}")
                    if r["ok"] else _err(f"✗ {r['error']}"))
        r = engine_ctrl.stop()
        return _ok("✓ Arrêté.") if r["ok"] else _err(f"✗ {r['error']}")

    # ── LLM toggle ────────────────────────────────────────────────────────

    @app.callback(
        Output("llm-status-indicator", "children"),
        Output("llm-status-indicator", "style"),
        Input("llm-on-btn",          "n_clicks"),
        Input("llm-off-btn",         "n_clicks"),
        Input("refresh-interval",    "n_intervals"),
        prevent_initial_call=False,
    )
    def _llm_toggle(_on, _off, _n):
        trig = dash.ctx.triggered_id
        if trig == "llm-on-btn":
            _api.set_llm(True)
        elif trig == "llm-off-btn":
            _api.set_llm(False)
        st = _api.engine_status()
        if st["llm_enabled"]:
            txt   = "LLM ACTIF"
            color = COLORS["accent"]
        else:
            txt   = "LLM INACTIF"
            color = COLORS["text"]
        return txt, {"fontSize": "11px", "color": color, "fontWeight": "700"}

    # ── LLM mode toggle (Phase-6) ─────────────────────────────────────────

    @app.callback(
        Output("llm-mode-result", "children"),
        Input("llm-mode-off-btn",      "n_clicks"),
        Input("llm-mode-observer-btn", "n_clicks"),
        Input("llm-mode-risk-btn",     "n_clicks"),
        prevent_initial_call=True,
    )
    def _llm_mode(_off, _obs, _risk):
        trig = dash.ctx.triggered_id
        if trig == "llm-mode-off-btn":
            _api.set_llm_mode("OFF")
            return _ok("✓ LLM mode → OFF")
        if trig == "llm-mode-observer-btn":
            _api.set_llm_mode("OBSERVER")
            return _ok("✓ LLM mode → OBSERVER")
        if trig == "llm-mode-risk-btn":
            _api.set_llm_mode("RISK_OVERLAY")
            return _ok("⚠ LLM mode → RISK_OVERLAY")
        return dash.no_update

    # ── Quick strategy toggles panel ──────────────────────────────────────

    @app.callback(
        Output("strat-quick-toggles", "children"),
        Input("refresh-interval", "n_intervals"),
        Input({"type": "qt-toggle", "index": ALL}, "n_clicks"),
    )
    def _quick_toggles(_n, _clicks):
        # Handle toggle click first
        trig = dash.ctx.triggered_id
        if isinstance(trig, dict) and trig.get("type") == "qt-toggle":
            name = trig["index"]
            live = {s.get("name"): s for s in load_strategy_status()}
            s = live.get(name, {})
            currently_active = s.get("state", "DISABLED") in ("ACTIVE", "SUSPENDED")
            if currently_active:
                _api.disable_strategy(name)
            else:
                _api.enable_strategy(name)

        # Render current state
        live = {s.get("name"): s for s in load_strategy_status()}
        _STATE_C = {
            "ACTIVE":    COLORS["success"],
            "SUSPENDED": COLORS["warning"],
            "DISABLED":  "#444",
            "KILLED":    COLORS["danger"],
            "ENGINE OFF": "#333",
        }
        now = time.time()
        status_age = None
        try:
            _sf = _REPO / "runtime" / "strategy_status.json"
            if _sf.exists():
                status_age = now - _sf.stat().st_mtime
        except Exception:
            pass
        stale = status_age is not None and status_age > 30

        pills = []
        for name in _ALL:
            s = live.get(name, {})
            if stale and not s:
                state = "ENGINE OFF"
            elif s:
                state = s.get("state", "DISABLED")
            else:
                state = "DISABLED"
            active = state in ("ACTIVE", "SUSPENDED")
            c = _STATE_C.get(state, "#444")
            accent = STRAT_COLORS.get(name, COLORS["accent"])
            pills.append(
                dbc.Button(
                    [
                        html.Span("●  " if active else "○  ",
                                  style={"color": c, "fontSize": "10px"}),
                        html.Span(name, style={"fontSize": "10px", "fontWeight": "600",
                                               "color": accent if active else "#666"}),
                        html.Span(f"  [{state}]",
                                  style={"fontSize": "9px", "color": c,
                                         "marginLeft": "4px", "fontWeight": "400"}),
                    ],
                    id={"type": "qt-toggle", "index": name},
                    color="link",
                    style={
                        "border": f"1px solid {c if active else '#333'}",
                        "borderRadius": "3px",
                        "backgroundColor": "#111" if active else "#080808",
                        "padding": "3px 8px",
                        "margin": "2px",
                        "cursor": "pointer",
                        "textDecoration": "none",
                    },
                    title=f"Cliquer pour {'désactiver' if active else 'activer'} {name}",
                )
            )
        return html.Div(pills, style={"display": "flex", "flexWrap": "wrap"})

    # ── Engine log viewer ─────────────────────────────────────────────────

    @app.callback(
        Output("engine-log-box", "children"),
        Input("refresh-interval", "n_intervals"),
    )
    def _engine_log(_n):
        log_path = _REPO / "logs" / "engine_stdout.log"
        if not log_path.exists():
            return "— Aucun log moteur pour l'instant —"
        try:
            with open(log_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
            last = lines[-60:]  # dernières 60 lignes
            return "".join(last)
        except Exception as e:
            return f"Erreur lecture log: {e}"

    # ── Refresh badges + capital + positions (DataTable NOT overwritten) ──

    @app.callback(
        *[Output(f"badge-{s}",    "children") for s in _ALL],
        *[Output(f"badge-{s}",    "style")    for s in _ALL],
        *[Output(f"cap-disp-{s}", "children") for s in _ALL],
        *[Output(_pd(s),           "children") for s in _ALL],
        *[Output(f"ldg-{s}",      "children") for s in _ALL],
        Input("refresh-interval", "n_intervals"),
        State("cap-store", "data"),
    )
    def _refresh_all(_n, cap_store):
        cap_store = cap_store or {}
        live = {s.get("name"): s for s in load_strategy_status()}
        now  = time.time()
        import datetime as _dt
        _STATE_COLOR = {
            "ACTIVE":    COLORS["success"],
            "SUSPENDED": COLORS["warning"],
            "DISABLED":  COLORS["danger"],
            "KILLED":    COLORS["danger"],
        }
        # Stale threshold: if engine last updated status >30s ago, mark as ENGINE OFF
        status_age = None
        try:
            _sf = _REPO / "runtime" / "strategy_status.json"
            if _sf.exists():
                status_age = now - _sf.stat().st_mtime
        except Exception:
            pass
        stale = status_age is not None and status_age > 30

        badges, badge_styles, cap_disps, pos_divs, ldg_divs = [], [], [], [], []
        for name in _ALL:
            s    = live.get(name, {})
            dflt = _DEF[name]

            if stale and not s:
                state_str = "ENGINE OFF"
            elif s:
                state_str = s.get("state", "DISABLED")
            else:
                state_str = "DISABLED"

            sc = _STATE_COLOR.get(state_str, COLORS["text"])
            badges.append(state_str)
            badge_styles.append({"backgroundColor": sc, "fontSize": "9px",
                                  "padding": "2px 6px"})
            # Priority: user override (store) > engine live > code default
            stored     = cap_store.get(name)
            engine_cap = s.get("capital_allocated_usd") if s else None
            if stored is not None:
                cap = stored
            elif engine_cap is not None:
                cap = engine_cap
            else:
                cap = dflt["capital"]
            cap_disps.append(f"${cap:.0f}")
            pos_divs.append(_render_positions(name, s.get("open_positions", []) if s else []))
            # Ledger panel — enriched with suspension time + pending count
            ldg = dict(s.get("ledger", {})) if s else {}
            if ldg:
                ldg["pending_orders_count"] = s.get("pending_orders_count", 0)
                susp_ts = float(s.get("suspended_until", 0) or 0)
                if susp_ts > now:
                    ldg.setdefault(
                        "suspended_until_readable",
                        _dt.datetime.fromtimestamp(susp_ts).strftime("%H:%M:%S"),
                    )
            ldg_divs.append(_render_ledger(ldg))
        return (*badges, *badge_styles, *cap_disps, *pos_divs, *ldg_divs)

    # ── Action buttons (14 strategies × 7 actions = 98 inputs) ──────────

    @app.callback(
        *[Output(_fb(s), "children") for s in _ALL],
        *[Input(f"en-{s}",  "n_clicks") for s in _ALL],
        *[Input(f"dis-{s}", "n_clicks") for s in _ALL],
        *[Input(f"dco-{s}", "n_clicks") for s in _ALL],
        *[Input(f"dfl-{s}", "n_clicks") for s in _ALL],
        *[Input(f"res-{s}", "n_clicks") for s in _ALL],
        *[Input(f"rst-{s}", "n_clicks") for s in _ALL],
        *[Input(f"flt-{s}", "n_clicks") for s in _ALL],
        prevent_initial_call=True,
    )
    def _strat_actions(*_clicks):
        trig  = dash.ctx.triggered_id
        empty = [""] * len(_ALL)
        if not trig:
            return empty
        for prefix, label_fn, api_fn in [
            ("en-",  lambda n: f"▶ {n} activé",                  _api.enable_strategy),
            ("dis-", lambda n: f"⏸ {n} désactivé",               _api.disable_strategy),
            ("dco-", lambda n: f"⏸+✗ {n} cancel pending",        _api.disable_strategy_cancel),
            ("dfl-", lambda n: f"⏸+⚡ {n} flatten",               _api.disable_strategy_flatten),
            ("res-", lambda n: f"▶ {n} suspension levée",        _api.reset_strategy),
            ("rst-", lambda n: f"↺ {n} streak à zéro",           _api.reset_strategy),
            ("flt-", lambda n: f"⚡ {n} flatten",                 _api.flatten_strategy),
        ]:
            if trig.startswith(prefix):
                name = trig[len(prefix):]
                try:
                    api_fn(name)
                except Exception as exc:
                    if name in _ALL:
                        empty[_ALL.index(name)] = _err(f"✗ {exc}")
                    return empty
                if name in _ALL:
                    empty[_ALL.index(name)] = _ok(f"✓ {label_fn(name)} — ~5s")
                return empty
        return empty

    # ── Set capital (9 buttons) — writes Store so auto-refresh won't revert ─

    @app.callback(
        *[Output(_fb(s),           "children", allow_duplicate=True) for s in _ALL],
        *[Output(f"cap-disp-{s}", "children", allow_duplicate=True) for s in _ALL],
        Output("cap-store", "data", allow_duplicate=True),
        *[Input(f"setcap-{s}",    "n_clicks") for s in _ALL],
        *[State(f"cap-in-{s}",    "value")    for s in _ALL],
        State("cap-store", "data"),
        prevent_initial_call=True,
    )
    def _set_caps(*args):
        n  = len(_ALL)
        _clicks = args[:n]
        vals    = args[n:2*n]
        store   = dict(args[2*n] or {})
        trig      = dash.ctx.triggered_id
        empty_fb  = [""] * n
        empty_cap = [dash.no_update] * n
        if not trig:
            return (*empty_fb, *empty_cap, dash.no_update)
        name = trig[len("setcap-"):]
        if name not in _ALL:
            return (*empty_fb, *empty_cap, dash.no_update)
        idx = _ALL.index(name)
        try:
            val = float(vals[idx])
            assert val >= 0
        except (TypeError, ValueError, AssertionError):
            empty_fb[idx] = _warn("⚠ Montant invalide")
            return (*empty_fb, *empty_cap, dash.no_update)
        _api.set_capital(name, val)
        store[name]    = val           # persist in browser session store
        empty_fb[idx]  = _ok(f"✓ Capital {name} → ${val:.0f}")
        empty_cap[idx] = f"${val:.0f}"
        return (*empty_fb, *empty_cap, store)

    # ── Apply params (9 buttons) ──────────────────────────────────────────

    @app.callback(
        *[Output(_fb(s), "children", allow_duplicate=True) for s in _ALL],
        *[Input(f"apply-{s}", "n_clicks") for s in _ALL],
        *[State(_dt(s),       "data")     for s in _ALL],
        prevent_initial_call=True,
    )
    def _apply_params(*args):
        n  = len(_ALL)
        _clicks, datas = args[:n], args[n:]
        trig  = dash.ctx.triggered_id
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

    # ── Manual position close (pattern-matched) ───────────────────────────

    @app.callback(
        *[Output(_fb(s), "children", allow_duplicate=True) for s in _ALL],
        Input({"type": "close-pos", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _close_position(n_clicks_list):
        trig  = dash.ctx.triggered_id
        empty = [""] * len(_ALL)
        if not trig or not any(c for c in (n_clicks_list or []) if c):
            return empty
        parts = trig["index"].split("|", 1)
        strat_name, pos_id = (parts[0], parts[1]) if len(parts) == 2 else ("", trig["index"])
        try:
            _api.close_position(pos_id)
        except Exception as exc:
            if strat_name in _ALL:
                empty[_ALL.index(strat_name)] = _err(f"✗ {exc}")
            return empty
        if strat_name in _ALL:
            empty[_ALL.index(strat_name)] = _ok(
                f"✓ Close {strat_name}/{pos_id[:8]} — ~5s")
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

    # ── Reset modal open/close ─────────────────────────────────────────────

    @app.callback(
        Output("reset-confirm-modal", "is_open"),
        Input("reset-all-btn",     "n_clicks"),
        Input("reset-cancel-btn",  "n_clicks"),
        Input("reset-confirm-btn", "n_clicks"),
        State("reset-confirm-modal", "is_open"),
        prevent_initial_call=True,
    )
    def _toggle_reset_modal(_open, _cancel, _confirm, is_open):
        trig = dash.ctx.triggered_id
        if trig == "reset-all-btn":
            return True
        return False

    # ── Reset action ──────────────────────────────────────────────────────

    @app.callback(
        Output("global-cmd-result", "children", allow_duplicate=True),
        Output("cap-store",         "data",     allow_duplicate=True),
        Input("reset-confirm-btn",  "n_clicks"),
        prevent_initial_call=True,
    )
    def _do_reset(_n):
        # 1. Stop engine
        engine_ctrl.stop()

        # 2. Delete all data files
        deleted, errors = [], []
        for p in _RESET_FILES:
            try:
                if p.exists():
                    p.unlink()
                    deleted.append(str(p))
                    print(f"[RESET] deleted: {p}")
            except OSError as e:
                errors.append(f"{p.name}: {e}")
                print(f"[RESET] ERROR deleting {p}: {e}")

        # 3. Clear data_loader in-memory cache so graphs update within 5s
        _cache.clear()
        _json_cache.clear()

        if errors:
            return _err(f"✗ Reset partiel — {', '.join(errors)}"), dash.no_update
        n = len(deleted)
        msg = _ok(f"✓ Reset complet ({n} fichiers). Relancer le moteur via ▶ DÉMARRER.")
        return msg, {}   # empty dict clears all capital overrides
