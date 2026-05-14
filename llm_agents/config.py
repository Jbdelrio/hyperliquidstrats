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

# ── Three-mode safety switch (Phase-6) ─────────────────────────────────
# OFF           — LLM completely bypassed, decisions pass through unchanged.
# OBSERVER      — LLM is called (if configured), result is LOGGED only.
#                 Trade decisions are NEVER modified.
# RISK_OVERLAY  — LLM can BLOCK or REDUCE size by 50 %. It can NEVER
#                 increase size and NEVER create or flip a decision.
# Read at engine start; also overridable at runtime via
# runtime/llm_mode.json (the engine control loop picks it up).
ARTEMISIA_LLM_MODE = os.environ.get("ARTEMISIA_LLM_MODE", "OFF").upper()
if ARTEMISIA_LLM_MODE not in ("OFF", "OBSERVER", "RISK_OVERLAY"):
    ARTEMISIA_LLM_MODE = "OFF"
LLM_MODE = ARTEMISIA_LLM_MODE

# Risk flags that always block trading
BLOCKING_RISK_FLAGS = frozenset({
    "high_spread",
    "extreme_volatility",
    "insufficient_data",
    "llm_error",
    "llm_parse_error",
    "bad_execution_venue",
})
