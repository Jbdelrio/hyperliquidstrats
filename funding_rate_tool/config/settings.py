"""Global settings — overridable via environment variables (see .env.example)."""
from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _env_int(key: str, default: int) -> int:
    try:
        return int(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.environ.get(key, default))
    except (TypeError, ValueError):
        return default


ROOT_DIR = Path(__file__).resolve().parent.parent
CACHE_DIR = ROOT_DIR / ".cache"
CACHE_DIR.mkdir(exist_ok=True)
CACHE_DB_PATH = CACHE_DIR / "funding_rates.db"

CACHE_TTL_SECONDS = _env_int("FRT_CACHE_TTL_SECONDS", 300)
REQUEST_TIMEOUT = _env_int("FRT_REQUEST_TIMEOUT", 10)
RATE_LIMIT_DELAY = _env_float("FRT_RATE_LIMIT_DELAY", 0.2)

GUI_PORT = _env_int("FRT_GUI_PORT", 9000)
GUI_HOST = os.environ.get("FRT_GUI_HOST", "127.0.0.1")
GUI_REFRESH_MS = _env_int("FRT_GUI_REFRESH_MS", 30_000)

LOG_LEVEL = os.environ.get("FRT_LOG_LEVEL", "INFO").upper()

DEFAULT_COINS = ["BTC", "ETH", "SOL"]
DEFAULT_EXCHANGES = ["binance", "aster", "bitget", "okx", "gateio", "hyperliquid", "kraken", "bitmex"]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) FundingRateTool/1.0"
)

# Cyborg (Bootswatch) palette — see https://bootswatch.com/cyborg/
DARK_THEME = {
    "background": "#060606",
    "card": "#222222",
    "border": "#282828",
    "text": "#adafae",
    "text_dim": "#888888",
    "primary": "#2A9FD6",   # cyan accent
    "success": "#77B300",   # lime
    "danger": "#CC0000",
    "warning": "#FF8800",
}
