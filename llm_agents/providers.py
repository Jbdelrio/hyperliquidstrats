"""
llm_agents/providers.py — LLM provider abstraction.

DummyLLMProvider  : returns neutral response (always safe, no API key needed).
OpenAICompatibleProvider : calls any OpenAI-compatible REST API using `requests`.

No `openai` package required — uses `requests` which is already a dependency.
"""
from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)


class LLMProvider(ABC):
    @abstractmethod
    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        """Send prompt, return parsed JSON dict. Raise on unrecoverable error."""


class DummyLLMProvider(LLMProvider):
    """Pass-through provider when no API key is configured.

    Returns PASS (allow_trade=True, neutral probabilities) so strategies trade
    normally without an LLM — never blocks decisions.
    """

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        agent_name = user_payload.get("agent_name", "unknown")
        return {
            "agent_name": agent_name,
            "prob_up": 0.5,
            "prob_down": 0.5,
            "confidence": "low",
            "horizon_minutes": 60,
            "reasoning": "DummyLLMProvider — no real LLM configured, passing through",
            "risk_flags": ["dummy_provider"],
            "suggested_action": "PASS",   # ← never blocks trades
            "allow_trade": True,
            "expected_edge_bps": None,
        }


class OpenAICompatibleProvider(LLMProvider):
    """
    Calls an OpenAI-compatible chat completion endpoint.
    Works with OpenAI, Azure OpenAI, Ollama, LM Studio, Groq, etc.
    Uses `requests` only — no `openai` package required.
    """

    def __init__(self, api_key: str, base_url: str, model: str,
                 timeout: float = 20.0, max_retries: int = 2) -> None:
        self._api_key    = api_key
        self._base_url   = base_url.rstrip("/")
        self._model      = model
        self._timeout    = timeout
        self._max_retries = max_retries

    def complete_json(self, system_prompt: str, user_payload: dict) -> dict:
        import requests  # already in requirements.txt

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}",
        }
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            "temperature": 0.1,
            "max_tokens":  512,
            "response_format": {"type": "json_object"},
        }

        for attempt in range(self._max_retries + 1):
            try:
                resp = requests.post(
                    f"{self._base_url}/chat/completions",
                    headers=headers,
                    json=body,
                    timeout=self._timeout,
                )
                resp.raise_for_status()
                content = resp.json()["choices"][0]["message"]["content"]
                return json.loads(content)
            except (json.JSONDecodeError, KeyError, IndexError) as exc:
                log.warning("LLM JSON parse error (attempt %d): %s", attempt + 1, exc)
                if attempt == self._max_retries:
                    raise
            except Exception as exc:
                log.warning("LLM request error (attempt %d): %s", attempt + 1, exc)
                if attempt == self._max_retries:
                    raise
                time.sleep(1.0)

        raise RuntimeError("LLM provider: max retries exceeded")


def build_provider() -> LLMProvider:
    """Instantiate the appropriate provider based on environment config."""
    from llm_agents.config import (
        LLM_API_KEY, LLM_BASE_URL, LLM_MODEL,
        LLM_PROVIDER, LLM_TIMEOUT_SECONDS,
    )

    if LLM_PROVIDER == "dummy" or not LLM_API_KEY:
        if not LLM_API_KEY and LLM_PROVIDER != "dummy":
            log.warning(
                "LLM_API_KEY not set — falling back to DummyLLMProvider. "
                "Set LLM_PROVIDER=dummy to silence this warning."
            )
        return DummyLLMProvider()

    return OpenAICompatibleProvider(
        api_key=LLM_API_KEY,
        base_url=LLM_BASE_URL,
        model=LLM_MODEL,
        timeout=LLM_TIMEOUT_SECONDS,
    )
