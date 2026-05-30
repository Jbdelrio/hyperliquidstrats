# Implementation Summary — Trading frequency + observability pass

*2026-05-23*

This pass focused on **observability** (telling you precisely why the bot
isn't trading enough), **session hygiene** (no more 6000 → 9500 → 6000
flicker in the GUI), and an **automated parameter review** (rule-based v1,
LLM-pluggable later). The paper-executor refactor and A/B compare are
scoped but deferred — see §9.

---

## 1. Why the bot wasn't trading enough — the diagnosed picture

From `reports/ic_quickscan.md` (run 2026-05-21 on 496 800 rows of
`logs/seconds_features.csv`) and the diagnostic snapshot run today:

- **The microstructure signals do have predictive content** —
  `obi_1` IC = +0.19 at 5 s, decaying monotonically. Not noise.
- **But the predicted move is ~5× smaller than the round-trip cost.**
  Best decile-spread = +1.83 bps at 30 s vs. ~10 bps cost. Net negative
  expectation at the bar/seconds horizon for any taker strategy.
- **And the gates layered on top blocked even what *was* viable.** The
  diagnostic snapshot shows the dominant rejection reason is `cooldown`
  (>50 % of `FILTER_SKIP`s on OBImbalanceScalper). That's a tuning issue
  unrelated to alpha — too-long cooldowns idling the strategy.

So the answer to "why not enough trades?" is two-fold:
1. **Cost-vs-edge structural** (the IC scan) — won't be fixed by tuning;
   you need bigger moves (longer horizons), maker mode, or a real edge.
2. **Tuning of the gates** (the diagnostic) — fixable. The LLM review v1
   proposed 2 patches: reduce `cooldown_win_s` from 90 → 67.5 s and
   `cooldown_loss_s` from 240 → 180 s.

---

## 2. Files modified / created this pass

**Modified**
- `engine_v9.py` — writes `session_id` (uuid) + `started_at` to
  `runtime/engine_config.json`, plus a `runtime/heartbeat.json` at start.
- `gui/tabs/overview.py` — health pill row now shows **Session id** and
  **Engine up** age, in addition to the existing Data / Strats / Signals.

**Created**
- `scripts/diagnose_trading_frequency.py` — the central diagnostic tool.
- `scripts/run_llm_parameter_review.py` — rule-based v1 of the LLM
  recalibration agent (output schema stable, ready to swap in a real LLM).
- `tests/test_diagnostic_and_llm_review.py` — 5 tests, all passing.

**Untouched on purpose** — every protection layer (Sanity, MarketQualityGate,
DecisionThrottle, StrategyCapitalLedger, PortfolioRiskManager, KillSwitch,
ExecutionFilter) is still active. No safety floor was lowered without
evidence — this pass only proposes patches; nothing was auto-applied.

---

## 3. How to run the new tools

### Diagnostic — "why isn't it trading enough?"

```
python scripts/diagnose_trading_frequency.py
python scripts/diagnose_trading_frequency.py --minutes 10
python scripts/diagnose_trading_frequency.py --since 1779390020
```

Produces **`reports/trading_frequency_diagnostic.md`** with:
- per-coin feed health (books/s, trades/s, staleness)
- per-strategy table (raw signals, accepted, rejected, main rejection,
  trades/h, warmup, suggested action)
- top global rejection reasons
- gate-by-gate breakdown (MQG, DecisionThrottle, Ledger, KillSwitch, log MQG blocks)
- strategies that never trade — likely cause
- current market regime + any active regime adjustments
- headline counts (signals / placed / fills / trades-per-2min / WR / net PnL)

`--minutes N` first watches for N min, then snapshots — use to capture
a clean window. `--since EPOCH_OR_ISO` overrides the cutoff (default =
engine_config.started_at, with a pid-file fallback).

### LLM parameter review

```
python scripts/run_llm_parameter_review.py
python scripts/run_llm_parameter_review.py --min-rejects 50
```

Produces:
- **`reports/llm_parameter_review.md`** — per-strategy table + proposed
  patches with rationale, confidence, expected effect, risk.
- **`runtime/proposed_config_patch.json`** — machine-readable patches:
```json
{
  "strategy": "OBImbalanceScalper",
  "parameter": "execution_filters.cooldown_win_s",
  "current_value": 90,
  "proposed_value": 67.5,
  "reason": "cooldown accounts for 78% of rejections (1098); bounded -25% adjustment.",
  "expected_effect": {"trade_frequency": "increase", "risk": "low",
                       "net_pnl": "unknown_requires_ab_test"},
  "confidence": 0.80,
  "apply_automatically": false
}
```

**`apply_automatically` is hard-coded to `false`. The script never writes
to a live preset.** Review each patch, then apply manually.

### Run the bot (already in place)

```
restart_engine.bat                      # menu: normal / propre / stop seul
launch_gui.bat                          # menu: normal / fresh
```

Or in CLI:
```
python engine_v9.py --paper --config config/presets/paper_500_all_active.json
python -m gui.app
```

---

## 4. Reading the GUI overview (post-session_id)

The top pill row now shows:

| Pill | Meaning | Healthy |
|---|---|---|
| **Data:** | Age of `runtime/strategy_status.json` | `Feed OK (Xs)` ≤ 15 s |
| **Stratégies actives:** | n ACTIVE / n total | depends on preset |
| **Signaux aujourd'hui:** | Decisions since engine_start_ts | grows with run |
| **Session:** | First 12 chars of the engine's uuid | identical across reloads if same engine |
| **Engine up:** | `now - started_at` | grows linearly |

If `Session:` is `—` or `Engine up:` is stuck — the engine has died
since you opened the GUI (likely sleep — see `restart_engine.bat`). If
`Session:` changes between two refreshes, the engine restarted under you
(check `logs/engine_v9.log`).

**Equity / Trades / WR** in the headline cards are computed *only* from
fills since the current session's `started_at` (fix from the 2026-05-21
diagnostic). They don't blend old sessions any more.

---

## 5. Interpreting the LLM review

For each patch:
- **confidence** ≥ 0.70 — the rejection reason is dominant and the patch
  is likely to help. Worth trying.
- **0.40 ≤ confidence < 0.70** — directional hint, gather more data first.
- **confidence < 0.40** — not a strong claim, ignore for now.

For each patch's **risk** field:
- `low` — adjusts a timing / threshold knob with safety margin.
- `moderate` — touches a profitability floor (`min_expected_net_profit_usd`,
  `min_reward_risk_ratio`); validate in A/B before keeping.
- `high` — would relax a hard risk gate; the rule-set won't emit this in v1.

`net_pnl` is always `unknown_requires_ab_test` in v1 — the only honest
answer when the LLM hasn't simulated the change.

---

## 6. Currently proposed parameters (today's snapshot)

From `runtime/proposed_config_patch.json` after running on the current logs:

| Parameter | Current | Proposed | Confidence | Risk |
|---|---|---|---|---|
| `execution_filters.cooldown_win_s` | 90 | 67.5 | 0.80 | low |
| `execution_filters.cooldown_loss_s` | 240 | 180 | 0.80 | moderate |

**Nothing was changed in any preset.** To apply, edit
`config/presets/paper_500_all_active.json` manually, restart the engine
(`restart_engine.bat` → [2]), run for ≥ 2 h, then re-run the diagnostic
and compare.

---

## 7. Constraints respected

- **No live mode.** All presets remain `paper_mode: true`; `BTC_5MIN_BINARY_REPL`
  and `BTC_BINARY_HIGHLEV` are `paper_only: true, live_enabled: false`.
- **No gate removal.** Every gate is still in the cascade. Patches only
  propose *bounded* parameter changes.
- **No silent application.** `proposed_config_patch.json` is a proposal
  file; no script writes to live presets without a human-driven edit.
- **No fee/slippage masking.** The executor still applies them; the new
  scripts only read and report.
- **No session-mixing.** The fills filter from 2026-05-21 + new session_id
  display keep current and past sessions cleanly separated.

---

## 8. Tests

```
python -m pytest tests/ -q
```

**427 tests pass** (was 422; +5 from this pass).

New tests cover :
- diagnose script runs end-to-end + handles missing `engine_config.json`.
- LLM review proposes the right patch under a cooldown-dominated scenario.
- patch schema is complete (all required keys, types, `apply_automatically`
  always `false`).
- `min-rejects` correctly filters out low-volume strategies.

---

## 9. Deferred — clear plan for the next pass

### A. Paper executor 3-mode (`optimistic / realistic / conservative`)

The current `execution/high_freq_executor.py` already implements most of
"realistic" (MAKER_SIM/TAKER_SIM, `tp_fill_mode`, `partial_fills_enabled`,
slippage, fees, max_pending_seconds). Missing:
- explicit `mode` switch in the `paper_execution` block of the preset
- VWAP fill using `max_levels` L2 depth (currently single-level)
- `participation_rate` cap (max % of available depth per fill)
- `latency_ms` simulated as a pre-fill delay
- per-fill log rows with `intended_price / actual_fill_price / vwap_fill_price
  / fill_ratio / latency_ms / execution_mode / order_status / reason`

**Plan:** add a `PaperExecutionMode` enum and route the existing
`HighFreqExecutor` paths through it (decorate, don't rewrite). Keep the
default at `realistic` so existing tests keep their semantics.
Estimated effort: ~6-8 h, one substantial test file.

### B. A/B compare script

```
python scripts/compare_config_performance.py \
  --baseline config/presets/paper_500_total_seconds_filtered.json \
  --candidate runtime/proposed_config_patch.json \
  --logs logs/ --out reports/config_ab_report.md
```

**Plan:** the inputs already exist (preset JSON, patch JSON, fills + decisions
CSVs). The script:
1. Splits logs into two halves by timestamp (baseline run vs candidate run),
2. Computes per-half: trades/h, net PnL, Sharpe-ish, max DD, WR, expectancy,
   rejected signals, avg slippage, fees, capital usage, avg hold,
3. Renders a side-by-side Markdown table with deltas.

Estimated effort: 2-3 h. The diagnostic script's aggregation can be reused.

### C. Real LLM call in the parameter review

`_propose_patches()` in `scripts/run_llm_parameter_review.py` is the single
swap point. Replace its body with:
1. Build a prompt from `strat_dec`, `strat_perf`, `preset`, and the
   `_HEURISTICS` table (as context, not as instructions),
2. Call the user's preferred LLM with the schema spec inline,
3. Parse the JSON response, validate against the same schema,
4. Return the list of patches.

The schema validation in `tests/test_diagnostic_and_llm_review.py` already
locks the contract. Estimated effort: 2-3 h once the API key choice is made.

---

## 10. Changelog (this pass)

- `engine_v9.py` : `session_id` (uuid) + heartbeat at startup.
- `gui/tabs/overview.py` : 2 new pills (Session, Engine up).
- `scripts/diagnose_trading_frequency.py` : NEW.
- `scripts/run_llm_parameter_review.py` : NEW (rule-based v1).
- `tests/test_diagnostic_and_llm_review.py` : NEW, 5 tests.
- `reports/IMPLEMENTATION_SUMMARY.md` : NEW (this file).

No preset was modified. No protection was lowered. No strategy code was
changed. Live trading remains disabled at every layer.
