# LLM Overlay — Architecture & Usage

## Why LLM as an Overlay, Not a Trader

The LLM is a **probabilistic scoring filter**, not an autonomous trader.

- It **reads** market data → produces a probability + risk flags
- It **cannot** place orders, call exchange APIs, or modify leverage
- The execution engine decides whether to act based on the LLM's output
- If the LLM is unavailable or produces an error → the existing strategy runs unchanged

This follows the constraint that the LLM must not be a single point of failure for live capital.

## Architecture

```
Strategy.generate_signal()
    ↓
StrategyDecision (PLACE_BUY / PLACE_SELL / PLACE_QUOTES / ...)
    ↓
[LLM Overlay — optional gate]
    ├─ LLMOverlay.evaluate(MarketSnapshot) → LLMDecision
    │     ├─ PriceActionAgent         (momentum, trend, vol)
    │     ├─ MicrostructureAgent      (spread, OBI, funding)
    │     ├─ CrossExchangeAgent       (cross-exchange comparison)
    │     ├─ StrategyCriticAgent      (signal quality review)
    │     └─ RiskManagerAgent         (account risk gate)
    └─ combine_strategy_and_llm()
         ├─ BLOCK  → StrategyDecision(action="SKIP")
         ├─ SCALE  → StrategyDecision(notional_usd *= risk_multiplier)
         └─ CONFIRM→ StrategyDecision unchanged
    ↓
HighFreqExecutor.place_quotes() / paper simulator
```

## Two Architectures

### `independent_ensemble` (default)
- 5 agents run independently, receive the same MarketSnapshot
- Probabilities aggregated by **median** (robust to outliers)
- Risk flags merged (union)
- If RiskManagerAgent says NO_TRADE → blocked (unless `LLM_REQUIRE_RISK_APPROVAL=false`)

### `sequential_pipeline`
- Agents run in sequence: PriceAction → Microstructure → CrossExchange → StrategyCritic → RiskManager
- Each agent receives context from all previous agents
- Slower (sequential LLM calls) but more coherent reasoning
- Configure: `LLM_ARCHITECTURE=sequential_pipeline`

## Enabling the LLM Overlay

1. Copy `.env.example` → `.env`
2. Set `LLM_ENABLED=true`
3. Set `LLM_API_KEY=your_key` (or use `LLM_PROVIDER=dummy` for testing)
4. Run the engine normally — the overlay activates automatically

Without a key: the `DummyLLMProvider` returns neutral 50/50 decisions (NO_TRADE always).

## Variables

| Variable | Default | Purpose |
|---|---|---|
| `LLM_ENABLED` | `false` | Master switch |
| `LLM_PROVIDER` | `openai_compatible` | `openai_compatible` or `dummy` |
| `LLM_API_KEY` | `` | API key |
| `LLM_BASE_URL` | OpenAI | Compatible endpoint |
| `LLM_MODEL` | `gpt-4o-mini` | Model name |
| `LLM_ARCHITECTURE` | `independent_ensemble` | Ensemble or pipeline |
| `LLM_MIN_EDGE_PROB` | `0.57` | Min prob to generate LONG/SHORT bias |
| `LLM_MAX_DISAGREEMENT` | `0.18` | Agent disagreement cap |
| `LLM_REQUIRE_RISK_APPROVAL` | `true` | RiskManagerAgent veto |
| `LLM_LIVE_MODE_BLOCK_ON_ERROR` | `false` | Block trade if LLM unavailable |
| `LLM_LOG_PREDICTIONS` | `true` | Log to `data/llm_predictions.csv` |

## Reading the Dashboard (LLM Overlay Tab)

- **Status**: shows if LLM is on/off, which model/arch is running
- **Last Decisions**: per-symbol: action, P(up), confidence, allow_trade, flags
- **Rolling Brier Score**: < 0.20 = well-calibrated, > 0.25 = unreliable
- **Predictions Table**: full log with outcomes when filled in

## Brier Score & Calibration

The Brier Score measures probabilistic accuracy:
- BS = (prob_up - outcome)²
- BS = 0 → perfect predictions
- BS = 0.25 → equivalent to random (50/50)
- BS > 0.25 → worse than random

Rolling Brier over the last 50 predictions gives a live calibration signal.

Use `llm_agents/calibration.py::calibration_table()` for per-bucket analysis.

## Limits and Risks

- LLM calls add 2–20s latency per decision (mitigated: runs in a thread)
- API costs add up quickly at high signal rates — use sampling if needed
- LLM hallucination: prompts force JSON output and NO_TRADE on uncertainty
- The overlay does NOT backtest well — use paper mode to calibrate first

## Recommended Process

1. **Backtest** existing strategies without LLM to establish baseline
2. **Paper trade** with `LLM_ENABLED=true` and `DummyLLMProvider` (zero cost)
3. **Paper trade** with real LLM key and `LLM_MIN_EDGE_PROB=0.57`
4. **Monitor Brier Score** — activate scaling only when Brier < 0.22 over 100+ predictions
5. **Micro-size live** — `GLOBAL_LIVE_TRADING=true`, very small capital
6. **Scale** only if calibration is stable over 200+ predictions
