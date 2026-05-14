# Artemisia v9 — Sanity Audit Report

**Date:** 2026-05-14
**Scope:** engine, risk, execution, strategies, LLM overlay, GUI

This report documents the current state of the codebase, identifies bugs and
risks, and lists prioritized recommendations. It does **not** describe code
that has just been added in the same audit pass (e.g. SanityCheckEngine,
ExecutionPlanner, llm_agents/modes.py).

---

## 1. Architecture (text diagram)

```
                       ┌─────────────────────────────┐
                       │ Hyperliquid WebSocket feed │
                       └──────────────┬──────────────┘
                                      │ books + trades
                                      ▼
                    ┌─────────────────────────────────┐
                    │ OrderbookManager (data/)        │
                    │  - per-symbol best_bid/best_ask │
                    │  - mid, spread, depth           │
                    └────────────┬────────────────────┘
                                 │ stream_orderbook_updates / stream_trades
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│ EngineV9.run() — single asyncio event loop                          │
│                                                                     │
│   _orderbook_loop  → fill detection + per-strategy decision         │
│   _trade_loop      → trade-level sensor updates                     │
│   _minute_loop     → OHLCV bar dispatch (every 60s)                 │
│   _position_loop   → stop/TP/max_hold + expire stale orders (500ms) │
│   _watchdog_loop   → network + BTC vol guard (5s)                   │
│   _dashboard_loop  → terminal dashboard (60s)                       │
│   _control_loop    → GUI command bus via runtime/control.json (2s)  │
│   _arbitrage_monitor_loop                                            │
│                                                                     │
└───┬───────────────────────────────────────────────────────────────┬─┘
    │                                                               │
    │ strategies.on_*                                               │ execute_decision
    ▼                                                               ▼
┌────────────────┐   ┌──────────────────┐   ┌─────────────────────────────┐
│ Strategies (14)│   │ StrategyManager  │   │ Risk Gate Pipeline          │
│ Momentum, ...  │   │ - register/route │   │  1. ledger.can_open         │
└────────────────┘   └──────────────────┘   │  2. portfolio_risk.can_open │
                                            │  3. kill_switch.can_open    │
                                            │  4. execution_filter (RR)   │
                                            │  5. LLM overlay (optional)  │
                                            └──────────────┬──────────────┘
                                                           ▼
                                            ┌─────────────────────────────┐
                                            │ HighFreqExecutor (paper)    │
                                            │  - place_quotes             │
                                            │  - check_fills              │
                                            │  - expire_stale_orders      │
                                            │  - close_position           │
                                            └──────────────┬──────────────┘
                                                           ▼
                                            ┌─────────────────────────────┐
                                            │ Logs:                       │
                                            │  fills_v9.csv               │
                                            │  decisions_v9.csv           │
                                            │  risk_events.csv            │
                                            │  metrics_v9.csv             │
                                            └─────────────────────────────┘
```

---

## 2. Strengths

* Single asyncio event loop → no shared mutable state across threads, no
  asyncio locks needed.
* Multi-gate decision pipeline (ledger → portfolio → kill switch →
  execution filter → LLM) with each gate logging its rejection reason.
* Per-strategy capital ledger with realized + unrealized equity, daily DD
  suspension, total DD kill, peak-DD suspension.
* Realistic-fill simulator: latency, slippage, expiry, dynamic slippage by
  spread + notional.
* Atomic status file writes with Windows fallback.
* Live mode is gated by `--live` prompt **and** `NotImplementedError` in
  `HighFreqExecutor.__init__` → cannot accidentally route real orders.
* Micro-live mode requires explicit env var arm, max notional ≤ 5 USD,
  enforced at engine construction.
* 190 tests covering ledger, portfolio risk, executor realism, LLM
  schemas, exchange normalization, backtesting metrics, S8 EMS, etc.

---

## 3. Potential bugs

### 3.1 `engine_v9.py:701` — pause guard ordering
`_execute_decision` checks `self._pause_until` only inside the
`PLACE_*` branch. `CLOSE` and `CANCEL_QUOTES` are still executed during a
pause. That is actually intended (we always want to close), but the same
check does **not** verify the kill switch on close, which is fine but
worth documenting.

### 3.2 `engine_v9.py:706` — fallback notional
`requested_notional = decision.notional_usd or
strat.config.max_position_size_usd or 50.0`. The default of 50 is dead
code if `max_position_size_usd > 0`, but on a strategy with
`max_position_size_usd = 0` (e.g. disabled phase-2 strategies), the
hard-coded 50 USD becomes the size. Mitigated by ledger
`budget_exceeded` block, but the request is logged before the block.
Recommend treating `notional <= 0` as `SKIP` before any logging.

### 3.3 `engine_v9.py:818-830` — degenerate prices on PLACE_BUY/PLACE_SELL
`buy_p = 9_999_999.0` (PLACE_SELL) and `buy_p = 0.000_001` (PLACE_BUY for
SELL leg) are sentinel prices to ensure only the directional leg fills.
This is fragile: if `_apply_slippage` ever applies slippage to these
sentinel prices the resulting fill price would be wildly wrong. Today
`_apply_slippage` uses `base_price = best_ask/best_bid` not the limit
price, so the bug is dormant; flagged for future refactor.

### 3.4 `engine_v9.py:953` — `min_hold_s` skips protective closes by keyword
`_is_protective` checks for substrings `stop`, `manual`, `emergency`,
`flatten`, `shutdown` in `reason.lower()`. A strategy emitting CLOSE with
reason `"momentum_exit"` or `"time_stop"` will be **blocked** if less
than `min_hold_s` since entry — and the time stop reason "time_stop"
doesn't contain any of the keywords. For RSIBollingerReversion's
`time_stop` exit, this means the engine will silently swallow the exit
signal during the first 90 seconds. Recommend allowing all strategy CLOSE
signals to pass and only enforce `min_hold_s` on **early TP** or
discretionary closes.

### 3.5 `execution/high_freq_executor.py:305-311` — duplicate pair iteration
`expire_stale_orders` first removes by `self._pairs[pair_id]`, then has
a second loop that re-iterates `self._pending` to "catch any orders not
in the pair index". After the first loop, the orders have already been
popped, so the second loop is a no-op in practice. Harmless but
confusing.

### 3.6 `risk/kill_switch.py:117` — rampage uses `>` not `>=`
`if trades_last_hour > self.max_trades_ph` — config sets
`max_trades_per_hour=50`, the 51st trade triggers. Minor off-by-one,
keep but document.

### 3.7 `risk/strategy_capital_ledger.py:434` — daily reset by elapsed seconds
`_maybe_daily_reset` resets when `now - day_start_ts >= 86400`. This
means a strategy that's been running for 23h59m and then has a fill at
24h00m05s will reset, but it never aligns to UTC midnight. Acceptable
for a paper bot; document the behaviour.

### 3.8 `gui/tabs/overview.py:466` — pnl_day uses `ts > now - 86400`
Last 24h window, not "today" (UTC). The label "PnL today" is therefore
misleading. Recommend renaming to "PnL 24h" or implementing a real UTC
day bucket.

### 3.9 `llm_agents/coordinator.py:284` — multiplier scaling
`combine_strategy_and_llm` reduces notional by `mult` when `mult < 1.0`,
which is safe, but does not log the new value to `risk_events.csv`.
Audit trail loses the "LLM reduced by N%" event unless the engine logs
it elsewhere (which it doesn't today, only `[LLM] SCALE %s notional` at
DEBUG level).

### 3.10 `strategies/momentum_long_short.py:113` — TP/SL recomputed twice
On `on_orderbook_update` we compute tp_price/stop_price from `entry_price`
(approximated ask/bid) but `on_fill` recomputes them from actual `price`.
If the fill price differs from the quoted entry due to slippage, the
StrategyDecision.stop_loss / take_profit values logged in
`decisions_v9.csv` will not match the actual exits. Cosmetic, but
explains some apparent disagreement between decision log and fills log.

---

## 4. Config inconsistencies

### 4.1 `btc_move_5m_pct` — units mismatch (HIGH)
* `risk/kill_switch.py:142` compares `abs(prices[-1] - prices[0]) /
  prices[0]` (a **fraction**, e.g. 0.012 for 1.2%) against
  `self.btc_move_5m_pct`.
* `config/presets/paper_500_improved.json:148` sets `"btc_move_5m_pct":
  3.0` — interpreted as 300% move required, effectively disabling the
  guard.
* `config_v9.json` sets `"btc_move_5m_pct": 0.012` (1.2%) → correct.
* `config/presets/micro_live_safe.json` should be checked too.

Recommend: enforce a single unit (fraction in `[0, 1]`) at the config
boundary, raise on any value > 0.5.

### 4.2 `slippage_bps` mismatch between cost_filter and execution_filters
`CostFilter` defaults to `slippage_entry_bps=0.75 +
slippage_exit_bps=0.75` (1.5 bps round-trip), while
`execution_filters.slippage_bps` defaults to 4.0 bps and the
`HighFreqExecutor.base_slippage_bps` is 2.0. Three different defaults
for the same physical quantity. Recommend centralizing in a
`fees_and_slippage` config block.

### 4.3 `kill_after_consecutive_losses` granularity
Some presets set it to 4, others 5, others 6. With only one position at
a time and a long enough cooldown the difference rarely matters, but
recommend a single default (e.g. 4 for paper, 3 for micro-live).

### 4.4 `paper_simulation.paper_latency_ms` only used when present
If a preset omits the `paper_simulation` block, `engine_v9.py:158` falls
back to defaults (150ms / 30s taker / 120s maker / 2.0bps). Documented
in code comment but not in any preset.

---

## 5. Paper vs Live risks

* `HighFreqExecutor(paper=False)` raises `NotImplementedError` — good.
* `engine_v9.py:1630` requires the literal `CONFIRMED LIVE` prompt before
  even constructing the engine in live mode — good.
* Micro-live mode requires env var arm + max notional ≤ 5 USD — good.
* **Risk**: if a user manually edits `paper=True` in the constructor
  call (e.g. by importing `EngineV9` from a script and passing
  `paper=False`), the `NotImplementedError` is the only defense.
  Defense-in-depth: keep the `NotImplementedError` even if a live
  executor is added in the future; require both `paper=False` **and**
  `ARTEMISIA_ALLOW_LIVE=true` env var to instantiate it.

---

## 6. Capital accounting risks

### 6.1 Reserved → open promotion is per-pair_id
On fill, the engine looks up `pair_id` from `order_id[2:]` (stripping
"b_" or "s_" prefix). If `order_id` is shorter than 2 chars or doesn't
follow the prefix convention, the slice would silently yield a wrong
pair_id and the reservation would never promote. Today `_make_fill`
generates 8-char `uuid4()[:8]` ids and `_open_position_from_fill` uses
`order.order_id`, so the prefix is consistent. Brittle but functional.

### 6.2 No global "total open notional" gate
`KillSwitch.max_notional` caps the **sum across strategies**, but
`PortfolioRiskManager.max_net_exposure_pct` caps **net** (long − short).
A balanced book of $400 long + $400 short = $0 net but $800 gross
could pass both gates. With $500 paper capital, $800 gross is 1.6×
leverage. Recommend adding `max_gross_exposure_pct` to
PortfolioRiskManager. Lower priority because paper capital is small.

### 6.3 Reserved notional leak on engine restart
Reserved notional is in-memory only. If the engine crashes between
`reserve_notional` and a fill or expiry, the reservation is lost on
restart but the strategy ledger has been zeroed too, so net effect is
zero. OK.

---

## 7. Double position risks

* `HighFreqExecutor.place_quotes` blocks any second `pair` on the same
  symbol (`any(o.symbol == symbol for o in self._pending.values())`).
* `MomentumLongShort._positions[symbol]` is keyed by symbol, so the
  strategy itself won't try to open a second one.
* **However**, two **different** strategies can each open a position on
  the same symbol. PortfolioRiskManager handles same-direction
  correlation with `max_correlated_same_dir=2` (so a 3rd same-direction
  pos on the same coin is blocked). Document this behavior — it's
  intentional but can surprise.

---

## 8. Stale pending order risks

* `expire_stale_orders` is called in `_position_loop` every 500ms.
* TAKER orders expire after 30s by default, MAKER after 120s.
* If `expire_stale_orders` raises an exception the engine swallows it
  and continues, but if the loop is starved (e.g. orderbook_loop is
  in a tight burst), expirations can lag. Recommend a deadman-switch:
  if no `expire_stale_orders` call in the last 60s, force-cancel all.

---

## 9. Disabled strategy still active risks

* `StrategyManager.on_orderbook_update` checks `if not strat.enabled:
  continue` — disabled strategies don't produce decisions.
* `StrategyManager.check_position_exits` does **not** check `enabled` —
  intentional, so a strategy can still close its open positions after
  being disabled. Good.
* **Risk**: if a strategy is disabled at runtime via `_disable_strategy`
  with `mode="disable_only"`, its open positions keep running and its
  capital remains reserved/open in the ledger. The GUI badge correctly
  shows `DISABLED` but PnL keeps flowing. Document this and consider
  adding a UI warning ("positions still managed").

---

## 10. LLM in hot path risks

* `_apply_llm_overlay_sync` is offloaded to `asyncio.to_thread` →
  doesn't block the asyncio event loop.
* `LLM_TIMEOUT_SECONDS=20` is the per-API-call timeout. The orderbook
  loop processes ~10-50 updates/sec; if every decision spawns a
  20-second thread, the threadpool will saturate.
* `LLM_SAMPLE_RATE` defaults to 1.0 (every call). For a $500 paper
  setup this is fine because decisions are rare; for any larger
  configuration recommend reducing to ≤ 0.1.
* **Critical**: `combine_strategy_and_llm` may change `decision.action`
  to `"SKIP"` (good — blocks the trade) but it also has a path that
  scales notional by `mult` which is always in [0, 1]. The hard cap to
  ≤ 1.0 is enforced in `LLMDecision.__post_init__` — good, but a
  defensive double-clip in `combine_strategy_and_llm` would prevent any
  future regression.

---

## 11. Recommendations

### P0 — Must fix before any live exposure

1. **SanityCheckEngine** (this audit pass). Centralized first-line
   sanity checks on every StrategyDecision: book sanity, spread limit,
   notional limits, stop/TP presence, RR ratio, daily loss limits,
   stale book / heartbeat, hourly/daily trade count caps.
2. **Strict LLM modes** (this audit pass): `OFF`, `OBSERVER`,
   `RISK_OVERLAY`. Hard-cap multiplier ≤ 1.0 in `llm_agents/modes.py`,
   defensive validation via Pydantic, double-clip in apply path.
3. **ExecutionPlanner** (this audit pass): decide MAKER vs TAKER
   policy, set `max_pending_s` per order type, enforce stop/TP
   presence for directional orders.
4. **Enriched StrategyDecision schema** (this audit pass): `signal_id`,
   `confidence`, `risk_usd`, `reward_risk_ratio`, `expected_edge_bps`,
   `estimated_cost_bps`, `expected_net_profit_usd`, `strategy_family`,
   `order_type`, `time_in_force`, `requires_llm_review`.
5. **Audited 500$ paper preset** (`paper_500_total_safe.json`, this
   audit pass): 3 strategies, 1 position max, 100$ max notional,
   conservative thresholds.

### P1 — Should fix in next sprint

6. Wire `orders_v9.csv` logging in HighFreqExecutor (fills, expires,
   cancels). Currently only fills go to a CSV; expires/cancels are
   only logged at DEBUG.
7. BreakoutControlled: add `close_strength` filter to require the
   close to be in the upper portion of the breakout bar.
8. RSIBollingerReversion: explicit `set_btc_context()` setter to inject
   the 5m BTC return from the engine.
9. MomentumLS: add `min_score_threshold` param so that a coin must
   exceed an absolute (not just relative) score before being traded.
10. Tests for SanityCheckEngine, LLM modes, ExecutionPlanner, enriched
    schema, and 500$ preset (this audit pass).
11. Fix `min_hold_s` keyword-list bug (#3.4) so `time_stop` /
    `momentum_exit` reasons are not silently blocked.
12. Unify `btc_move_5m_pct` units (#4.1) — raise on value > 0.5.

### P2 — Nice to have

13. `max_gross_exposure_pct` in PortfolioRiskManager (#6.2).
14. Rename "PnL today" to "PnL 24h" or fix to true UTC day (#3.8).
15. Add deadman-switch on `expire_stale_orders` (#8).
16. Centralize fee/slippage config (#4.2).
17. Add UI warning when a disabled strategy still has open positions
    (#9).
18. GUI Orders tab + LLM mode toggle (this audit pass).
19. `scripts/analyze_llm_value_added.py` for LLM A/B analysis
    (this audit pass).
20. `docs/DEPLOYMENT_PLAYBOOK.md` 5-phase rollout (this audit pass).

---

**End of report.**
