# Strategies readiness — paper launch 2026-05-24

*Audit + recalibration de l'ensemble des 21 stratégies du preset
`config/presets/paper_500_all_active.json`. Objectif user : tout impeccable
avant de lancer le bot paper pour plusieurs heures de collecte.*

## Headline

| Métrique | Avant | Après |
|---|---:|---:|
| Strats `enabled: true` | 21 | **14** |
| Strats `enabled: false` | 0 | **7** |
| Capital actif | $8 500 | **$6 000** |
| Capital libéré (réalloué au cap global) | — | $2 500 |
| Tests | 432 ✓ | **441 ✓** (+9) |
| Bugs latents corrigés | 0 | **4** (`_disabled_reason` ignoré) |

## Strats désactivées (7)

### Pour cause de sub-cost en taker (3)
Le `reports/alpha_research_report.md` (scan IC) montre que ces strats prédisent
1-2 bps de move pour ~10 bps de coût round-trip → négatif-EV structurel.

- **OBImbalanceScalper** — sub-cost
- **BookFlowDivergenceReversal** — sub-cost
- **AbsorptionReversal** — sub-cost

### Bug latent corrigé : `_disabled_reason` ignoré (4)
Ces 4 strats avaient un `_disabled_reason` clair dans le commentaire mais
`enabled: true`. L'engine lit uniquement `enabled` → elles tournaient malgré
le flag. **Corrigé : passé à `enabled: false` avec mention CORRIGÉ 2026-05-24.**

- **S8EMS** — maker market-maker, spam `spread_too_tight` sur les majors
- **FundingArbitrage** — directional carry (pas hedged) → risque
- **SpotPerpBasis** — pas de spot feed wired → signal fake
- **RelativeValue** — 2-leg execution untested

## Strats actives par priorité (14)

### 🔴 Priorité user — recalibrées (5)

| Strat | Changement clé | Attendu (paper) |
|---|---|---|
| **BTC_5MIN_BINARY_REPL** | min_rv 8→3, long_thr 0.56→0.53, early-exit 0.50→0.42 + grace 90s | ~15 trades/h (= cap) |
| **BTC_BINARY_HIGHLEV** | Même class, calibration **opposée** sur exits (100× lev = tight) : early-exit 0.45/0.55, grace 30s | ~20 trades/h (= cap) |
| **MomentumLS** | Cooldown global 90→30s (débloque les 127 sig/h écrasés), univers trimmé 12→5 coins | 5-15 trades/h |
| **AlphaPressureScalper** | `maker_only: true` (cost 9→3.5 bps), threshold 0.35→0.18, notional 50→100 | 10-30 sig/h |
| **VolatilityRegimeBreakout** | SL 1.0%→0.7%, TP 1.8%→1.4%, max_hold 4h→3h, vol_thr 25→30 bps, univers 10→6 | ~5-10 trades/h |

### 🟡 Bar strats secondaires — recalibrées (3)

| Strat | Changement clé | Attendu |
|---|---|---|
| **DonchianTrend** | donchian_n 20→14, vol_mult 1.0→0.5, min_cost_ratio 1.5→1.0 | 1-5 trades/jour |
| **BreakoutControlled** | lookback 10→14, vr_min 0.8→0.3 | 1-5 trades/jour |
| **RSIBollingerReversion** | rsi_oversold 35→40, zscore -1.5→-1.0, bb_k 1.8→1.5 | 1-5 trades/jour |

### 🟢 Strats restant inchangées (4)

- **MeanReversionKalman** — 3 sig/h dans le diag, SL/TP raisonnables, MQG-filtré
  (normal pour MR contre le flow).
- **RotationMomentum** — `autonomous: false`, scanner seulement, ne trade pas.
- **MetaAlpha** — quorum-based meta-strat, agira selon les peers actifs.
- **FundingCarryHedged** — recalibrée la veille
  (cf. `reports/funding_carry_diagnostic.md` + `memory/funding-carry-recalibration.md`).

### Scanners (capital=0)

- **SecondsResearch** — feeds GUI calibration tab.
- **FundingArbEnhanced** — univers étendu (13 coins) hier 2026-05-23.

## Changements transverses

### Cooldown global coupé
`execution_filters.cooldown_win_s` : **90 → 30s**
`execution_filters.cooldown_loss_s` : **240 → 90s**

Justification : les 3 scalpers seconds qui avaient besoin de cooldown long
sont désactivés. Les strats restantes ont des hold horizons de minutes
(scalpers maker, BTC_BINARY) à heures (bar strats), donc 30s/90s est
largement suffisant. Avant cette coupe, MomentumLS générait 127 sig/h tous
rejetés par cooldown.

### Code-level : nouveau params dans `btc_5min_binary_repl.py`
4 params exposés au lieu des hardcoded :
- `early_exit_signal_p_up_long_below` / `_short_above`
- `early_exit_flow_magnitude`
- `min_hold_seconds_before_early_exit`

Defaults = valeurs hardcoded originales → backward compatible. Les deux
strats BTC_*_BINARY peuvent désormais tuner leurs exits indépendamment.

## Ce qui peut encore mal tourner en paper

1. **Coûts maker mode (AlphaPressureScalper)** — le `paper_simulation` du
   moteur simule maker fills via une queue conservative. La réalité peut être
   moins favorable (no-fill rate plus élevé). Surveiller le ratio
   `fills / decisions` dans `logs/decisions_v9.csv` après quelques heures.

2. **Early exits BTC_BINARY_HIGHLEV** — à 100× lev avec $1 SL (10 bps adverse),
   30s de grace period peut être risqué. Si les premiers trades hitent SL
   plus que TP, raccourcir la grace à 15s ou 0s.

3. **Volatility regime breakout** — 789/790 trades du backtest hitaient
   max_hold. La recal raccourcit max_hold 4h→3h et resserre SL/TP mais ne
   garantit pas que la dynamique s'inverse. Surveiller la distribution des
   exit_reasons après quelques heures.

4. **MomentumLS univers trimmé à 5** — moins de dispersion = moins de
   diversité d'opportunités. Si fréquence < 5 trades/h, élargir progressivement.

5. **Bar strats relâchés** — DonchianTrend / BreakoutControlled /
   RSIBollingerReversion étaient muets en backtest. Les relâchements peuvent
   les faire trader mais l'edge n'est pas validée. Considérer ces 3 comme
   data-collection à $1500 risk.

## Procédure de validation paper

```
restart_engine.bat   # menu : [2] propre
# laisser tourner 2-4h
python scripts/diagnose_trading_frequency.py --minutes 5
# lire reports/trading_frequency_diagnostic.md : per-strategy rejection mix,
# trades/h, MQG breakdown
```

**Critères d'acceptation après 4h :**
- Aucune strat SUSPENDED par kill-switch sans raison
- BTC_5MIN_BINARY_REPL : ≥ 5 trades/h, exit_reasons mix (pas que EARLY_EXIT_*)
- MomentumLS : ≥ 3 trades/h
- AlphaPressureScalper : ≥ 5 sig/h (fills dépendent du maker queue)
- VolatilityRegimeBreakout : exit_reasons distribution avec ≥ 10% TP/SL
  (pas 99% max_hold comme avant)
- Total fills > 50 sur 4h

**Si l'un échoue, NE PAS lancer plus longtemps** — diagnostiquer l'écart et
ajuster avant prolongation.

## Mémoires créées / mises à jour pendant cette passe

- `funding-carry-recalibration.md` (la veille)
- `focus-strategies.md` (5 priority strats, dont BTC_BINARY_HIGHLEV ajouté)
- `btc-5min-binary-audit.md` (audit + recal détaillée)
- `MEMORY.md` index mis à jour

## Annexes

- État détaillé du preset : `config/presets/paper_500_all_active.json` (tous
  les changements ont un `_comment` daté 2026-05-24 expliquant la
  justification).
- Test suite : `python -m pytest tests/` → 441 ✓.
