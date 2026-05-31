# Diagnostic du run paper — 2026-05-31

Analyse quantitative de `logs/fills_v9.csv` (148 trades cumulés depuis 2026-05-26,
multi-sessions) + `runtime/`. Engine PID 32316, config `paper_500_all_active.json`.

## TL;DR — trois problèmes, par ordre d'importance

1. **La stratégie validée (ZEC HourlyBreakout) ne tourne PAS.** Le set actif
   (`runtime/engine_config.json → selected_strategies`) ne contient ni
   `H1Breakout_ZEC`, ni WLD/HYPE. Le moteur fait tourner l'**ancien zoo**
   (MomentumLS, OBImbalanceScalper, MeanReversionKalman, BTC binaries, AlphaDecile)
   — exactement les stratégies qu'on avait déjà identifiées comme sans edge. **Le
   vrai test n'a jamais commencé.**

2. **Le P&L est 100% des frais, l'edge brut est nul.**
   - Gross total : **−$4.68** · Fees : **$22.14** · Net : **−$26.82**
   - Seulement **−21%** des frais sont « récupérés » par l'edge brut → autrement
     dit l'edge directionnel est nul/négatif ; on paie le spread+fees sur des
     trades sans direction.

3. **Tes exits sortent effectivement trop tôt (confirmé), mais c'est secondaire.**
   - `MeanReversionKalman` : 11 sorties `stop` à **0.84 s de hold moyen** (!!) —
     le stop est à l'intérieur du bruit/spread, touché instantanément.
   - BTC binaries : sorties `early` = le **plus gros poste de perte (−$14)**, hold
     164 s — les déclencheurs d'early-exit sont trop sensibles (déjà noté au 05-24).
   - Scalpers (`imbalance_reversed` 34 s, `z_reversion` 21 s) : coupés en 20-35 s,
     avant qu'un mouvement se développe → gross ≈ 0.
   - **Mais** : comme le gross est ≈ 0, mieux caler les exits ne rendrait pas ces
     stratégies rentables — ça ralentirait juste l'hémorragie.

## Détail par stratégie (depuis 2026-05-26)

| Stratégie | n | Gross $ | Fees $ | Net $ | AvgGross bps | WR % | Hold médian | Diagnostic |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| BTC_BINARY_HIGHLEV | 7 | −2.67 | 9.45 | −12.1 | −2.5 | 0% | 92 s | fees énormes (notional×levier), 0 edge |
| AlphaDecile_WLD_OBI120 | 14 | −2.36 | 2.10 | −4.5 | −6.8 | 29% | 120 s | edge négatif (déjà su) |
| MomentumLS | 11 | −1.51 | 2.48 | −4.0 | −5.5 | 0% | 517 s | 0/11 gagnants — direction systématiquement fausse |
| MeanReversionKalman | 34 | +0.04 | 2.04 | −1.9 | +0.1 | 3% | 20 s | gross ≈ 0 ; stops < 1 s |
| BTC_5MIN_BINARY_REPL | 10 | +0.19 | 2.10 | −1.9 | +0.8 | 0% | 209 s | early-exits cassent tout |
| OBImbalanceScalper | 63 | +0.25 | 1.89 | −1.6 | +0.8 | 14% | 23 s | imbalance reverse trop vite |
| AlphaDecile_INJ_LV300 | 7 | +0.42 | 1.58 | −1.2 | +2.4 | 29% | 300 s | léger gross+, mangé par fees |

Lecture : tous les `AvgGross` sont entre −7 et +2 bps. Aucun ne dépasse le coût
aller-retour (~9 bps taker / ~6 maker). **Pas d'edge → pas de rentabilité, quels
que soient les exits.**

## Pourquoi les exits sortent trop tôt (calibration)

- **MeanReversionKalman** (`z_entry=1.5, z_exit=0.2, z_stop=3.5`) : entrée quand la
  déviation au fair value Kalman atteint 1.5σ, sortie à 0.2σ ou stop à 3.5σ. Les
  stops à 0.84 s = la déviation **continue** au lieu de reverter (le « fair value »
  ne capture pas une vraie mauvaise valorisation sur ces majors liquides). Le
  z_stop=3.5σ est touché par un mouvement adverse immédiat.
- **OBImbalanceScalper** : sortie sur `imbalance_reversed`. L'imbalance de carnet
  se retourne en ~34 s (c'est du bruit micro), donc on entre et sort à plat.
- **BTC binaries** : `early_exit_*` triggers (p_up, flow) coupent à 164 s sur le
  moindre retournement de signal → matérialisent une perte avant que le mouvement
  de 3-5 min visé se forme.

→ **Diagnostic exits** : oui, les seuils de stop/early-exit sont trop serrés vs le
bruit du timeframe. **Mais le problème racine est l'absence d'edge brut**, pas les
exits. Élargir les stops sur une stratégie à gross nul ne crée pas d'alpha.

## Recommandations (par priorité)

1. **Relancer avec la bonne stratégie.** Arrêter le moteur, relancer sur
   `paper_500_hl1h_breakout.json` (ZEC seul, taker, warmup préchargé → trade tout
   de suite) — c'est le seul edge qu'on a validé OOS. Le watcher
   (`watch_strategy_performance.py`) dira si `AvgGross_ZEC ≥ ~9 bps`.
2. **Désactiver** les stratégies à gross nul du zoo (MeanReversionKalman,
   OBImbalanceScalper, MomentumLS, BTC_5MIN/HIGHLEV, AlphaDecile_*). Recalibrer
   leurs exits = optimiser une stratégie sans edge → perte de temps.
3. Si tu veux **garder** une scalper pour observation : élargir les stops bien
   au-delà du spread (MeanReversionKalman z_stop reverte trop ; passer z_exit à
   ~0.5 et z_stop à ~5, max_hold plus long) — mais ne pas lui allouer de capital
   tant que `AvgGross_live > coût` n'est pas démontré.
4. La doc complète des stratégies (formules, intuition, alpha) : voir
   `reports/STRATEGIES_EXPLAINED.md`.
