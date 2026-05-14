# Artemisia v9 — Deployment Playbook

This playbook describes the **5-phase rollout** required before any single
dollar of real capital is exposed. Each phase has explicit **entry
conditions**, **exit conditions** (= move to the next phase) and
**abort conditions** (= revert to the previous phase or stop).

Never skip a phase. Never compress phase durations.

---

## Phase 1 — Paper strict (LLM OFF)

**Goal:** validate that the engine + sanity checks + execution planner
behave correctly end-to-end on real market data, without any AI in the
loop.

**Configuration:**
- preset: `config/presets/paper_500_total_safe.json`
- env: `ARTEMISIA_LLM_MODE=OFF`
- 3 enabled strategies (MomentumLS, BreakoutControlled, RSIBollingerReversion)
- 1 open position max, $50 max notional per order

**Entry conditions (Phase 0 → Phase 1):**
- [ ] All 255+ unit tests green: `python -m pytest tests/ -v`
- [ ] `engine_v9.py` and `gui.app` import without errors
- [ ] `paper_500_total_safe.json` validates against
      `tests/test_config_500_safe.py`
- [ ] Logs directory clean (`scripts/analyze_logs.py` returns "no data")

**Duration:** at least **7 calendar days** of continuous running.

**Exit conditions (→ Phase 2):**
- [ ] No engine crash, no orphan asyncio task in `logs/engine_v9.log`
- [ ] Daily DD never exceeds 2.0% in any 24h window
- [ ] At least 20 closed trades total
- [ ] Sanity-check rejection rate < 30% of decisions
- [ ] No `risk_events.csv` row with `allowed=0` and `block_reason`
      containing `ledger_unknown_strategy`

**Abort conditions:**
- Any single trade with loss > 1.5% of capital
- Engine crash twice in 24h
- Strategy KILLED by total DD ≥ 5%
- Any "live mode" attempt (`paper=False`) in the codebase

---

## Phase 2 — Paper realistic (latency + partial fills)

**Goal:** stress-test the executor under realistic fill conditions.

**Configuration:**
- preset: `paper_500_total_safe.json`
- env: still `ARTEMISIA_LLM_MODE=OFF`
- Increase `paper_sim.latency_ms` to 300, `taker_expire_s` to 10
- Enable `base_slippage_bps = 5.0`

**Entry conditions:**
- All Phase 1 exits met
- `orders_v9.csv` has at least 50 FILL rows AND at least 5 EXPIRE rows

**Duration:** at least **3 days**, or until at least **100 closed trades**.

**Exit conditions (→ Phase 3):**
- [ ] Missed-fill rate (`EXPIRE` / total orders) < 25%
- [ ] Average slippage < 8 bps on TAKER fills
- [ ] No unbounded growth in `_pair_to_reserved` (engine memory)
- [ ] Expectancy per trade ≥ $0 (paper)

**Abort:**
- Missed fill rate > 50%
- Average slippage > 15 bps
- Negative expectancy after 100 trades

---

## Phase 3 — LLM observer

**Goal:** measure how often a real LLM would BLOCK or REDUCE trades, and
whether those decisions would have improved P&L.

**Configuration:**
- preset: `paper_500_total_safe.json`
- env: `ARTEMISIA_LLM_MODE=OBSERVER`, `LLM_ENABLED=true`,
        `LLM_SAMPLE_RATE=0.5`
- API key set, but engine still ignores all LLM verdicts

**Entry conditions:**
- All Phase 2 exits met
- LLM API key configured AND health-checked via
  `python scripts/diagnose_live.py` (if available)
- `logs/llm_decisions_v9.csv` initialised (header present, no data rows)

**Duration:** at least **3 days**, or until **300 LLM evaluations**.

**Exit conditions (→ Phase 4):**
- [ ] At least 100 LLM evaluations logged
- [ ] LLM never crashed the engine (`engine_v9.log` has no
      `LLM.*ERROR` rows)
- [ ] `analyze_llm_value_added.py` reports a measurable value-added
      score (positive or negative is fine — the metric exists)
- [ ] Average LLM call latency < 5 seconds

**Abort:**
- LLM availability < 80%
- Engine event loop blocked > 2 seconds at any point

---

## Phase 4 — LLM risk overlay

**Goal:** start letting the LLM BLOCK or REDUCE trades, with strict
sampling.

**Configuration:**
- preset: `paper_500_total_safe.json`
- env: `ARTEMISIA_LLM_MODE=RISK_OVERLAY`, `LLM_SAMPLE_RATE=0.10`
- LLM can only return CONFIRM, REDUCE_SIZE_50, or BLOCK

**Entry conditions:**
- All Phase 3 exits met
- `analyze_llm_value_added.py` shows that LLM-confirmed trades
  perform **at least as well** as the unfiltered set (no statistical
  evidence the LLM hurts P&L)

**Duration:** at least **5 days**.

**Exit conditions (→ Phase 5):**
- [ ] LLM block rate stays in [2%, 30%] (neither always-passthrough nor
      always-blocking)
- [ ] No regression in Phase-3 metrics (expectancy, daily DD)
- [ ] Multiplier never observed > 1.0 in `llm_decisions_v9.csv`
      (sanity check on the hard cap)

**Abort:**
- Multiplier > 1.0 ever observed → STOP, investigate, do not proceed
- LLM block rate > 50% (the model is broken / over-conservative)
- Daily DD worse than Phase-3 baseline

---

## Phase 5 — Micro-live ($5 notional)

**Goal:** very limited real-money exposure with airtight kill switches.

**Configuration:**
- preset: `config/presets/micro_live_safe.json` (paper_mode=false)
- env: `ARTEMISIA_ALLOW_MICRO_LIVE=true`,
        `ARTEMISIA_LLM_MODE=RISK_OVERLAY`
- $5 max notional per order, $3 max daily loss, 1 position max, maker only

**Entry conditions:**
- All Phase 4 exits met
- 14 calendar days of continuous paper running with positive expectancy
- Live-execution code path implemented AND code-reviewed (currently
  `NotImplementedError` — must be replaced)
- Multi-signature approval (two operators) for live arm

**Duration:** indefinite, but reviewed weekly.

**Stop conditions (DO NOT auto-resume):**
- Any daily DD ≥ 60% of `max_daily_loss_usd`
- Any single trade slippage > 50 bps
- LLM block rate < 1% (LLM is asleep) or > 60% (LLM is broken)
- Two consecutive losing days

**Promotion to standard live:** out of scope of this playbook. Requires
a dedicated risk review + the entire pipeline rebuilt for variable
position sizing.

---

## Checklist summary

| Phase | LLM Mode      | Notional   | Min Duration | Key Metric                          |
|-------|---------------|------------|--------------|-------------------------------------|
| 1     | OFF           | $50 paper  | 7 days       | No crash, DD < 2%                   |
| 2     | OFF           | $50 paper  | 100 trades   | Missed fill < 25%, slippage < 8bps  |
| 3     | OBSERVER      | $50 paper  | 300 evals    | LLM stable, latency < 5s            |
| 4     | RISK_OVERLAY  | $50 paper  | 5 days       | Block rate ∈ [2%, 30%]              |
| 5     | RISK_OVERLAY  | $5 LIVE    | review weekly| Slippage < 50bps, no DD spike       |
