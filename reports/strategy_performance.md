# Strategy Performance Report

- Fills    : `C:\Users\jeanb\Documents\Mercantour\hyperliquidstrats\artemisia_v9\logs\fills_v9.csv` (32 rows)
- Regime   : `logs/regime_adaptations.csv` (108 rows)

## 1. Aggregate
- Trades              : 32
- Net PnL             : $-16.0227
- Total fees          : $11.6250
- Win rate            : 18.75%
- Expectancy / trade  : $-0.5007  (net, after fees)
- **Avg gross / trade** : $-0.1374  (**-2.78 bps** — the honest edge; must exceed the round-trip cost to be net-positive)
- Profit factor       : 0.13
- Max drawdown        : $-15.7107
- Avg fees / trade    : $0.3633
- Avg hold (s)        : 2363

## 2. Per strategy
| strategy               |   trades |   net_total |   expectancy |   avg_gross_bps |   win_rate |   fees_total |   avg_hold_s |   profit_factor |
|:-----------------------|---------:|------------:|-------------:|----------------:|-----------:|-------------:|-------------:|----------------:|
| BTC_5MIN_BINARY_REPL   |        6 |     -1.0417 |      -0.1736 |          1.0552 |     0.0000 |       1.2000 |     214.3500 |          0.0000 |
| AlphaDecile_INJ_LV300  |        7 |     -1.1584 |      -0.1655 |          2.3805 |     0.2857 |       1.5750 |   10320.1000 |          0.5911 |
| AlphaDecile_WLD_OBI120 |       14 |     -4.4630 |      -0.3188 |         -6.7514 |     0.2857 |       2.1000 |     120.2214 |          0.1551 |
| BTC_BINARY_HIGHLEV     |        5 |     -9.3596 |      -1.8719 |         -3.4795 |     0.0000 |       6.7500 |      81.6800 |          0.0000 |

## 3. Per symbol
| symbol   |   trades |   net_total |   expectancy |   win_rate |
|:---------|---------:|------------:|-------------:|-----------:|
| INJ      |        7 |     -1.1584 |      -0.1655 |     0.2857 |
| WLD      |       14 |     -4.4630 |      -0.3188 |     0.2857 |
| BTC      |       11 |    -10.4013 |      -0.9456 |     0.0000 |

## 4. Cost diagnostics
- Avg total fees / trade : $0.3633
- Avg exit slippage bps  : 0.00
- Fees / (|net| + fees)  : 42.0%

## 5. Frequency
- Trades / hour : 1.49
- Window (h)    : 21.51

## 6. Performance per regime
| regime        |   trades |   net_total |   expectancy |   win_rate |
|:--------------|---------:|------------:|-------------:|-----------:|
| LOW_VOL_RANGE |        1 |     -0.3178 |      -0.3178 |     0.0000 |

## 7. Recent parameter adaptations (last 30)
|              ts | strategy               | symbol   | regime        | param_name      |   old_value |   new_value | reason             |
|----------------:|:-----------------------|:---------|:--------------|:----------------|------------:|------------:|:-------------------|
| 1779833726.9420 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779833726.9470 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779833726.9510 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779833850.3400 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779833850.3400 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779833850.3460 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779834189.3500 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779834189.3530 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779834189.3570 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779834436.5410 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779834436.5430 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779834436.5450 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779834498.6110 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779834498.6120 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779834498.6150 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779834621.5990 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779834621.6020 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779834621.6040 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779834745.5290 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779834745.5320 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779834745.5350 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779835316.0670 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779835316.0690 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779835316.0710 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779835474.2880 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779835474.2900 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779835474.2930 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
| 1779835535.4700 | AlphaPressureScalper   | MARKET   | LOW_VOL_RANGE | take_profit_bps |     42.0000 |     33.6000 | low_vol_tp_tighten |
| 1779835535.4710 | AlphaDecile_INJ_LV300  | MARKET   | LOW_VOL_RANGE | take_profit_bps |    100.0000 |     80.0000 | low_vol_tp_tighten |
| 1779835535.4720 | AlphaDecile_WLD_OBI120 | MARKET   | LOW_VOL_RANGE | take_profit_bps |     70.0000 |     56.0000 | low_vol_tp_tighten |
