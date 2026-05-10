"""
theme.py — Cyborg colour palette and chart helpers for Artemisia v9 GUI.
"""
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import html

THEME = dbc.themes.CYBORG

COLORS = {
    "bg":         "#060606",
    "card_bg":    "#1a1a1a",
    "accent":     "#2A9FD6",
    "success":    "#77B300",
    "danger":     "#CC0000",
    "warning":    "#FF8800",
    "text":       "#ADAFAE",
    "text_light": "#FFFFFF",
    "grid":       "#2a2a2a",
    "card_bg2":   "#0d0d0d",
}

# Per-strategy accent colours for charts/badges
STRAT_COLORS = {
    "S8EMS":                 "#2A9FD6",   # blue
    "MomentumLS":            "#9B59B6",   # purple
    "BreakoutControlled":    "#27AE60",   # green
    "MeanReversionKalman":   "#E67E22",   # orange
    "FundingArbitrage":      "#E74C3C",   # red
    "DonchianTrend":         "#1ABC9C",   # teal
    "RSIBollingerReversion": "#F39C12",   # amber
    "RotationMomentum":      "#8E44AD",   # violet
    "RelativeValue":         "#95A5A6",   # grey (paper only)
}


def apply_dark_theme(fig, height: int = None) -> go.Figure:
    """Apply Cyborg dark styling to a plotly figure in-place."""
    layout_kw = dict(
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["card_bg"],
        font_color=COLORS["text"],
        margin=dict(l=40, r=20, t=35, b=40),
        legend=dict(bgcolor=COLORS["card_bg"], bordercolor=COLORS["grid"],
                    font=dict(size=11)),
    )
    if height is not None:
        layout_kw["height"] = height
    fig.update_layout(**layout_kw)
    fig.update_xaxes(gridcolor=COLORS["grid"], linecolor=COLORS["grid"],
                     zerolinecolor=COLORS["grid"])
    fig.update_yaxes(gridcolor=COLORS["grid"], linecolor=COLORS["grid"],
                     zerolinecolor=COLORS["grid"])
    return fig


def gauge_fig(value: float, min_val: float, max_val: float, title: str,
              warn_pct: float = 0.5, danger_pct: float = 0.75,
              unit: str = "%", height: int = 180) -> go.Figure:
    """Semicircle gauge (Indicator) with colour zones."""
    range_size = max_val - min_val
    warn_lvl   = min_val + range_size * warn_pct
    danger_lvl = min_val + range_size * danger_pct
    value_clamped = max(min_val, min(max_val, value))

    if value_clamped >= danger_lvl:
        bar_color = COLORS["danger"]
    elif value_clamped >= warn_lvl:
        bar_color = COLORS["warning"]
    else:
        bar_color = COLORS["success"]

    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value_clamped,
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": title, "font": {"color": COLORS["text"], "size": 11}},
        number={"font": {"color": bar_color, "size": 20},
                "suffix": unit},
        gauge={
            "axis": {"range": [min_val, max_val],
                     "tickcolor": COLORS["text"], "tickfont": {"size": 9}},
            "bar":  {"color": bar_color, "thickness": 0.22},
            "bgcolor": COLORS["bg"],
            "bordercolor": COLORS["grid"],
            "steps": [
                {"range": [min_val,   warn_lvl],   "color": "#0a1a0a"},
                {"range": [warn_lvl,  danger_lvl],  "color": "#1a1000"},
                {"range": [danger_lvl, max_val],    "color": "#1a0000"},
            ],
            "threshold": {
                "line": {"color": COLORS["danger"], "width": 2},
                "thickness": 0.7,
                "value": danger_lvl,
            },
        },
    ))
    fig.update_layout(
        paper_bgcolor=COLORS["bg"],
        font_color=COLORS["text"],
        margin=dict(l=10, r=10, t=30, b=10),
        height=height,
    )
    return fig


def no_data(msg: str = "En attente des premiers logs...") -> html.Div:
    return html.Div(
        msg,
        style={
            "color": COLORS["text"],
            "padding": "40px",
            "textAlign": "center",
            "fontStyle": "italic",
            "fontSize": "13px",
        },
    )


def stat_card(title: str, value: str, color: str = None,
              subtitle: str = None) -> dbc.Card:
    children = [
        html.P(title, className="text-muted mb-1",
               style={"fontSize": "0.72rem", "textTransform": "uppercase",
                      "letterSpacing": "0.5px"}),
        html.H5(value, style={"color": color or COLORS["accent"],
                               "marginBottom": 0, "fontFamily": "Consolas,monospace"}),
    ]
    if subtitle:
        children.append(html.Small(subtitle, style={"color": COLORS["text"],
                                                     "fontSize": "0.68rem"}))
    return dbc.Card(
        dbc.CardBody(children),
        className="card-glow",
        style={"backgroundColor": COLORS["card_bg"],
               "border": f"1px solid {COLORS['grid']}"},
    )
