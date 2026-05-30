# HourlyBreakout — validation backtest (cached HL 1h candles)

*2026-05-30T15:03:29 · net 9.0bps RT · ATR gate 25.0bps · channel ≥ 2.0× cost · lev 5.0x/$20*

## Base config — donchian_period=20, hold=4h

| Coin | n | total bps | train | test | WR% | avg bps | OOS+ | acct ret % | liq% |
|---|---:|---:|---:|---:|---:|---:|:--:|---:|---:|
| ZEC | 516 | +11868 | +5796 | +6072 | 48% | +23.0 | ✅ | +18.1% | 1% |

## Parameter sweep (test-half total bps) — is the edge robust?


### ZEC — test-half net bps by (period × hold)

| period \ hold | 2h | 4h | 6h | 12h |
|---|---|---|---|---|
| 10 |  **+8872**  |  **+8031**  |  **+8115**  |  -181  |
| 15 |  **+7830**  |  **+6558**  |  **+4851**  |  **+4494**  |
| 20 |  **+7874**  |  **+6072**  |  **+4346**  |  **+2854**  |
| 30 |  **+7296**  |  **+5079**  |  **+4655**  |  **+4216**  |
| 40 |  **+7028**  |  **+5418**  |  **+3549**  |  **+5739**  |

## Reading

- **OOS+** = train and test both positive at the base config. The sweep shows whether the test-half edge holds across (period × hold); a coin that is green across most of the grid is a real edge, a lone green cell is noise.

- Leverage modelled with liquidation (adverse path over the hold, loses 100% margin at `-0.5/L`). Keep `lev` ≤ the coin's HL maxLeverage.
