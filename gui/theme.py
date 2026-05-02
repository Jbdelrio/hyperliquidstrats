"""
theme.py — Cyborg colour palette and chart helpers for Artemisia v9 GUI.
"""
import dash_bootstrap_components as dbc
from dash import html

THEME = dbc.themes.CYBORG

COLORS = {
    "bg":       "#060606",
    "card_bg":  "#1a1a1a",
    "accent":   "#2A9FD6",
    "success":  "#77B300",
    "danger":   "#CC0000",
    "warning":  "#FF8800",
    "text":     "#ADAFAE",
    "text_light": "#FFFFFF",
    "grid":     "#2a2a2a",
}


def apply_dark_theme(fig):
    """Apply Cyborg dark styling to a plotly figure in-place."""
    fig.update_layout(
        paper_bgcolor=COLORS["bg"],
        plot_bgcolor=COLORS["card_bg"],
        font_color=COLORS["text"],
        margin=dict(l=40, r=20, t=35, b=40),
        legend=dict(bgcolor=COLORS["card_bg"], bordercolor=COLORS["grid"]),
    )
    fig.update_xaxes(gridcolor=COLORS["grid"], linecolor=COLORS["grid"])
    fig.update_yaxes(gridcolor=COLORS["grid"], linecolor=COLORS["grid"])
    return fig


def no_data(msg: str = "En attente des premiers logs...") -> html.Div:
    return html.Div(
        msg,
        style={
            "color": COLORS["text"],
            "padding": "40px",
            "textAlign": "center",
            "fontStyle": "italic",
        },
    )


def stat_card(title: str, value: str, color: str = None) -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([
            html.P(title, className="text-muted mb-1",
                   style={"fontSize": "0.75rem", "textTransform": "uppercase"}),
            html.H5(value, style={"color": color or COLORS["accent"], "marginBottom": 0}),
        ]),
        style={"backgroundColor": COLORS["card_bg"], "border": f"1px solid {COLORS['grid']}"},
    )
