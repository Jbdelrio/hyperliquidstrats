# LLM Parameter Review (rule-based v1)

*Generated 2026-05-23T16:44:29*

Preset analysed : `config/presets/paper_500_all_active.json`
Window since    : 2026-05-23T15:44:25

## 1. Per-strategy performance + rejection summary

| Strategy | Raw | Placed | Rejected | Main rejection | Trades | Net PnL | WR |
|---|---|---|---|---|---|---|---|
| (unattributed) | 172363 | 21513 | 150850 | `max_positions_reached` (129225) | 2 | $-0.0195 | 0% |
| MeanReversionKalman | 0 | 0 | 0 | — | 3 | $-0.1587 | 0% |
| MomentumLS | 0 | 0 | 0 | — | 3 | $-1.3452 | 0% |
| OBImbalanceScalper | 2 | 0 | 2 | `cooldown` (2) | 9 | $-0.3118 | 0% |

## 2. Proposed parameter patches

*No actionable patches — either samples are too small or all rejections fall under reasons without a rule (warmup, data_stale, etc.).*

## 3. Notes & caveats

- This is **risk-management tuning**, not alpha discovery. Lower thresholds let more trades fire; they do not create edge.
- Multiplications are bounded (`±15–35 %`). No floor is taken below a safe minimum here, but you should still validate each patch in A/B against the baseline.
- A patch with `confidence < 0.4` is a hint, not an instruction — needs more data.
- If `latency_p95` dominates rejections, the fix is in `data/orderbook_manager.py` (feed health), not in params.

---
*Rule-based v1 — swap `_propose_patches()` with an LLM call to upgrade.*