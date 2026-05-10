"""
llm_agents — LLM overlay package for Artemisia v9.

The LLM is a probabilistic scoring layer only.
It NEVER sends orders, NEVER touches the executor, NEVER increases position size.
Disabled by default (LLM_ENABLED=false). Safe fallback if any error occurs.
"""
from llm_agents.config import LLM_ENABLED  # noqa: F401
