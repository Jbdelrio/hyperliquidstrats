"""
llm_agents/config.py — All LLM overlay settings from environment variables.
Defaults keep the bot safe: LLM disabled, no API key required.
"""
import os


def _bool(key: str, default: str) -> bool:
    return os.environ.get(key, default).lower() in ("1", "true", "yes")


LLM_ENABLED               = _bool("LLM_ENABLED", "false")
LLM_PROVIDER              = os.environ.get("LLM_PROVIDER", "openai_compatible")
LLM_API_KEY               = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL              = os.environ.get("LLM_BASE_URL", "https://api.openai.com/v1")
LLM_MODEL                 = os.environ.get("LLM_MODEL", "gpt-4o-mini")
LLM_TIMEOUT_SECONDS       = float(os.environ.get("LLM_TIMEOUT_SECONDS", "20"))
LLM_ARCHITECTURE          = os.environ.get("LLM_ARCHITECTURE", "independent_ensemble")
LLM_HORIZON_MINUTES       = int(os.environ.get("LLM_HORIZON_MINUTES", "60"))
LLM_MIN_EDGE_PROB         = float(os.environ.get("LLM_MIN_EDGE_PROB", "0.57"))
LLM_MAX_DISAGREEMENT      = float(os.environ.get("LLM_MAX_DISAGREEMENT", "0.18"))
LLM_REQUIRE_RISK_APPROVAL = _bool("LLM_REQUIRE_RISK_APPROVAL", "true")
LLM_LIVE_MODE_BLOCK       = _bool("LLM_LIVE_MODE_BLOCK_ON_ERROR", "false")
LLM_LOG_PREDICTIONS       = _bool("LLM_LOG_PREDICTIONS", "true")
LLM_MAX_OHLCV_ROWS        = int(os.environ.get("LLM_MAX_OHLCV_ROWS", "60"))
LLM_USE_CROSS_EXCHANGE    = _bool("LLM_USE_CROSS_EXCHANGE", "true")
LLM_SAMPLE_RATE           = float(os.environ.get("LLM_SAMPLE_RATE", "1.0"))  # 1.0=always, 0.5=50%

# Risk flags that always block trading
BLOCKING_RISK_FLAGS = frozenset({
    "high_spread",
    "extreme_volatility",
    "insufficient_data",
    "llm_error",
    "llm_parse_error",
    "bad_execution_venue",
})
