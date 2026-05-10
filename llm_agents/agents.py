"""
llm_agents/agents.py — Five specialized LLM agents.

Each agent receives a compact MarketSnapshot and produces an AgentForecast.
All prompts enforce JSON-only output and prohibit order construction.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from llm_agents.schemas import AgentForecast, MarketSnapshot, parse_agent_forecast

log = logging.getLogger(__name__)

# ── Shared safety rules appended to every system prompt ─────────────────────
_SAFETY_BLOCK = """
STRICT RULES (must follow):
- Return ONLY valid JSON matching the schema below. No markdown, no explanation outside JSON.
- Do NOT recommend specific order sizes, leverage, or stop prices.
- Do NOT claim certainty. Use "low" confidence when data is insufficient.
- If data is insufficient or missing → suggested_action: "NO_TRADE", risk_flags include "insufficient_data".
- You are NOT allowed to construct exchange orders or call any API.
- You are NOT a financial advisor. This is a probabilistic risk filter only.

Output schema (strict JSON):
{
  "agent_name": "<string>",
  "prob_up": <float 0-1>,
  "prob_down": <float 0-1>,
  "confidence": "low" | "medium" | "high",
  "horizon_minutes": <int>,
  "reasoning": "<string, max 200 chars>",
  "risk_flags": ["<string>", ...],
  "suggested_action": "LONG" | "SHORT" | "NO_TRADE" | "REDUCE_ONLY",
  "expected_edge_bps": <float or null>
}
Note: prob_up + prob_down should sum to 1.0.
"""


def _snapshot_to_dict(snap: MarketSnapshot) -> dict:
    """Compact dict sent to LLM — only essential fields."""
    return {
        "symbol":               snap.symbol,
        "timestamp":            snap.timestamp,
        "exchange":             snap.exchange,
        "mid_price":            snap.mid_price,
        "best_bid":             snap.best_bid,
        "best_ask":             snap.best_ask,
        "spread_bps":           snap.spread_bps,
        "funding_rate":         snap.funding_rate,
        "open_interest":        snap.open_interest,
        "volume_24h":           snap.volume_24h,
        "volatility_short":     snap.volatility_short,
        "volatility_long":      snap.volatility_long,
        "orderbook_imbalance":  snap.orderbook_imbalance,
        "ohlcv_tail":           snap.ohlcv_tail[-10:],  # last 10 bars only
        "strategy_signals":     snap.strategy_signals,
        "current_position":     snap.current_position,
        "account_risk":         snap.account_risk,
    }


class BaseAgent:
    name: str = "base"
    system_prompt: str = ""

    def __init__(self, provider) -> None:
        self._provider = provider

    def run(self, snapshot: MarketSnapshot,
            context: Optional[dict] = None) -> AgentForecast:
        payload = _snapshot_to_dict(snapshot)
        if context:
            payload["prior_context"] = context
        payload["agent_name"] = self.name

        try:
            raw = self._provider.complete_json(
                self.system_prompt + _SAFETY_BLOCK,
                payload,
            )
            return parse_agent_forecast(raw, self.name)
        except Exception as exc:
            log.warning("[%s] LLM call failed: %s", self.name, exc)
            return AgentForecast(
                agent_name=self.name,
                prob_up=0.5,
                prob_down=0.5,
                confidence="low",
                horizon_minutes=60,
                reasoning=f"error: {exc!s}"[:200],
                risk_flags=["llm_error"],
                suggested_action="NO_TRADE",
            )


class PriceActionAgent(BaseAgent):
    name = "PriceActionAgent"
    system_prompt = """You are a quantitative price action analyst for crypto perpetual futures.
Analyze the provided market data and classify the short-term directional probability.
Focus on: momentum (recent returns), trend direction, volatility regime, distance to recent
high/low, and coherence of technical signals.
Horizon: 30–120 minutes ahead.
"""


class MicrostructureAgent(BaseAgent):
    name = "MicrostructureAgent"
    system_prompt = """You are a market microstructure analyst for crypto perpetual futures.
Analyze bid-ask spread, orderbook imbalance, volume patterns, funding rate, and open interest.
Assess execution quality and slippage risk.
Flag: high_spread if spread_bps > 8; extreme_volatility if vol_short > 2.0 (annualised);
volume_burst if volume anomaly detected; funding_extreme if |funding_rate| > 0.001.
Horizon: 15–60 minutes ahead.
"""


class StrategyCriticAgent(BaseAgent):
    name = "StrategyCriticAgent"
    system_prompt = """You are a strategy critic reviewing signal quality for a multi-strategy
crypto trading bot. Examine the strategy_signals field.
Identify: signal conflicts between strategies, weak or contradictory signals,
market conditions where the triggered strategy is historically fragile.
If signals conflict significantly → suggested_action: NO_TRADE.
If only one strategy signals and others are silent → reduce confidence.
Horizon: same as triggered strategy.
"""


class RiskManagerAgent(BaseAgent):
    name = "RiskManagerAgent"
    system_prompt = """You are a risk manager for a crypto trading bot.
Your job is to approve, flag, or veto new trade entries based on risk.
Analyze: account exposure (open_positions, notional_open vs equity),
daily drawdown (daily_dd_pct), volatility, spread, and leverage implied by notional_usd.
Rules:
- If daily_dd_pct > 0.02 → REDUCE_ONLY or NO_TRADE.
- If spread_bps > 8 → risk_flag: high_spread, NO_TRADE.
- If volatility_short > 1.5 (annualised) → risk_flag: extreme_volatility, NO_TRADE.
- If open_positions >= 10 → risk_flag: position_limit, NO_TRADE.
- If account_risk is None or data insufficient → NO_TRADE.
NEVER suggest increasing leverage or position size.
Horizon: immediate (gate decision).
"""


class CrossExchangeAgent(BaseAgent):
    name = "CrossExchangeAgent"
    system_prompt = """You are a cross-exchange market quality analyst.
You compare market conditions across Hyperliquid, Binance, and Bitget.
The primary exchange is Hyperliquid (execution venue).
Analyze cross_exchange_data to detect:
- Price divergence: flag price_deviation_high if |price_diff_bps| > 15.
- Spread quality: flag bad_execution_venue if target exchange spread is >50% worse than best.
- Funding disagreement: flag funding_divergence if funding rates differ significantly.
- Orderbook imbalance contradictions: flag cross_imbalance_conflict.
- Confirmation: if all exchanges agree on direction → note in reasoning, do NOT increase size.
If cross_exchange_data is None or empty → risk_flag: no_cross_exchange_data, confidence: low.
Signal weak_cross_exchange_confirmation if signal exists on primary only.
NEVER suggest arbitrage orders or cross-exchange execution.
Horizon: 30–90 minutes.
"""

    def run(self, snapshot: MarketSnapshot,
            context: Optional[dict] = None) -> AgentForecast:
        if not snapshot.cross_exchange_data:
            return AgentForecast(
                agent_name=self.name,
                prob_up=0.5,
                prob_down=0.5,
                confidence="low",
                horizon_minutes=60,
                reasoning="no cross-exchange data available",
                risk_flags=["no_cross_exchange_data"],
                suggested_action="NO_TRADE",
            )
        payload = _snapshot_to_dict(snapshot)
        payload["cross_exchange_data"] = snapshot.cross_exchange_data
        payload["available_exchanges"] = snapshot.available_exchanges
        if context:
            payload["prior_context"] = context
        payload["agent_name"] = self.name
        try:
            raw = self._provider.complete_json(
                self.system_prompt + _SAFETY_BLOCK,
                payload,
            )
            return parse_agent_forecast(raw, self.name)
        except Exception as exc:
            log.warning("[CrossExchangeAgent] LLM call failed: %s", exc)
            return AgentForecast(
                agent_name=self.name,
                prob_up=0.5,
                prob_down=0.5,
                confidence="low",
                horizon_minutes=60,
                reasoning=f"error: {exc!s}"[:200],
                risk_flags=["llm_error"],
                suggested_action="NO_TRADE",
            )


def build_agents(provider) -> dict[str, BaseAgent]:
    return {
        "price_action":     PriceActionAgent(provider),
        "microstructure":   MicrostructureAgent(provider),
        "strategy_critic":  StrategyCriticAgent(provider),
        "risk_manager":     RiskManagerAgent(provider),
        "cross_exchange":   CrossExchangeAgent(provider),
    }
