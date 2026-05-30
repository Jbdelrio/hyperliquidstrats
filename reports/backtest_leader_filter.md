# Leader-bias filter on HourlyBreakout — A/B

*2026-05-30T14:39:21 · net 6.0bps · leader window 4h · A good filter lifts avg_bps & WR (fewer, better trades).*


## ZEC (period 20, hold 4h)

| Variant | n | total bps | avg bps | WR % | train | test | OOS+ |
|---|---:|---:|---:|---:|---:|---:|:--:|
| baseline | 517 | +13635 | +26.4 | 49% | +6942 | +6693 | ✅ |
| veto_opposite@40 | 472 | +10559 | +22.4 | 49% | +6185 | +4374 | ✅ |
| veto_opposite@25 | 448 | +6565 | +14.7 | 48% | +3333 | +3232 | ✅ |
| require_agree@40 | 358 | +2636 | +7.4 | 47% | +1546 | +1090 | ✅ |

## WLD (period 20, hold 6h)

| Variant | n | total bps | avg bps | WR % | train | test | OOS+ |
|---|---:|---:|---:|---:|---:|---:|:--:|
| baseline | 419 | +94 | +0.2 | 48% | -2320 | +2413 | — |
| veto_opposite@40 | 409 | -1941 | -4.7 | 49% | -3628 | +1688 | — |
| veto_opposite@25 | 396 | -2298 | -5.8 | 49% | -3435 | +1138 | — |
| require_agree@40 | 333 | -1969 | -5.9 | 48% | -1803 | -166 | — |

## HYPE (period 20, hold 6h)

| Variant | n | total bps | avg bps | WR % | train | test | OOS+ |
|---|---:|---:|---:|---:|---:|---:|:--:|
| baseline | 444 | +394 | +0.9 | 47% | -731 | +1124 | — |
| veto_opposite@40 | 416 | +1537 | +3.7 | 48% | +1600 | -62 | — |
| veto_opposite@25 | 395 | +2787 | +7.1 | 49% | +794 | +1993 | ✅ |
| require_agree@40 | 320 | -1178 | -3.7 | 48% | -551 | -627 | — |

## Verdict

**The leader-bias filter is NOT generic free alpha — it is coin-specific and
mostly hurts.** Default stays OFF (`leader_bias_enabled: false`).

- **ZEC** — filter **harms** (avg +26.4 → +22.4 → +14.7 → +7.4 as it tightens).
  ZEC's breakout edge is idiosyncratic, not BTC-coupled; vetoing counter-leader
  breakouts just removes good trades. **Keep OFF.**
- **WLD** — filter **harms** (goes negative). **Keep OFF.**
- **HYPE** — filter **helps** at `veto_opposite@25`: avg +0.9 → +7.1, WR 47→49%,
  total +394 → +2787, and OOS+ (test +1124 → +1993). HYPE is more BTC-coupled, so
  breakouts fighting BTC fail more. **Optional ON for HYPE only** — but it's a
  single-window result; reconfirm before trusting.

Takeaway: a leader veto helps only where the coin is genuinely coupled to BTC/ETH
and the host signal is weak. On a strong idiosyncratic momentum coin (ZEC) it
strictly costs you trades. The filter is now available as an opt-in param; it is
not enabled in `paper_500_hl1h_breakout.json`.

## Reading

- The filter is worth keeping if a gated variant **raises avg bps/trade and WR** vs baseline (it trades less but cleaner). If avg barely moves, the leader info is already in the breakout — drop it.

- `veto_opposite` only blocks breakouts fighting a strong leader move; `require_agree` is stricter (needs a leader pushing the same way).
