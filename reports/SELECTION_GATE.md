# Porte de sélection finale — verdicts OOS (PHASE 6)

Critères GO (TOUS requis) : AvgNet_bps OOS > 0 après 14 bps · plateau paramétrique · survie au stress 15 bps · edge déflaté (Deflated Sharpe) positif · breadth ≥ min_coins. Aucun tuning sur l'in-sample. **Seules les GO sont éligibles au paper trading.**

## ✅ / 🟡 GO (éligibles paper)

| Stratégie | Conf | AvgNet OOS | DSR | Breadth | Statut |
|---|---|---:|---:|---|---|
| Breakout 1h — ZEC seul | HIGH | +70.27 bps | 0.86 | 1/1 | 🟡 GO (non sig. 95% / à confirmer) |

## ❌ NO-GO (raison chiffrée)

| Stratégie | Conf | AvgNet OOS | Raison |
|---|---|---:|---|
| Breakout 1h — décile haut-vol (8 coins) | HIGH | +11.23 bps | edge déflaté ≤ seuil multiple-testing (SR=0.025 ≤ SR0=0.046) |
| B6 Décile (maker) | LOW | -0.49 bps | AvgNet_bps OOS ≤ 0 (-0.49) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-0.99) ; edge déflaté… |
| D1 Binaire BTC 5x | LOW | -4.32 bps | AvgNet_bps OOS ≤ 0 (-4.32) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-4.82) ; edge déflaté… |
| B6 Décile (taker) | LOW | -8.70 bps | AvgNet_bps OOS ≤ 0 (-8.70) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-9.20) ; edge déflaté… |
| Réversion funding 1h | HIGH | -8.75 bps | AvgNet_bps OOS ≤ 0 (-8.75) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-9.75) ; edge déflaté… |
| Trend EMA-cross 4h | HIGH | -50.85 bps | AvgNet_bps OOS ≤ 0 (-50.85) ; pas de plateau (pic isolé = overfit) ; ne survit pas au stress 15bps (-51.85) ; edge défla… |

## ⏳ Non testé (à venir / donnée manquante)

| Stratégie | Intervalle | Note |
|---|---|---|
| CrossSectionalReversal | 15m/1h | réversion transversale (réutilise momentum_long_short inversé) — adaptateur panel à écrire |
| LiquidationCascadeReversal | 1m/15m | cascade de liquidation (range>k·ATR + spike volume) — adaptateur à écrire |
| ResidualBTCReversion | 15m/1h | réversion du résidu alt~BTC — adaptateur 2-actifs à écrire |
| MarkOracleDislocation | 1m/15m | DONNÉE INDISPONIBLE : oracle historique non exposé par l'API HL → testable seulement en live |

## Résumé priorisé (français)

- **Edge net OOS confirmé (ferme, GO significatif)** : *aucun*. Sous walk-forward purgé + déflation multiple-testing + stress de coût, **aucune stratégie ne passe le seuil de significativité à 95 %** sur le TOP 20.
- **Provisoire / à confirmer** : **Breakout 1h — ZEC seul** est la meilleure (AvgNet +70.27 bps OOS) mais reste **non significative après déflation** → edge probablement spécifique/sur-ajusté, à ne PAS sur-pondérer. C'est la seule à garder en observation paper (petit capital).
- **Rejeté** : Trend EMA-cross 4h (whipsaw, −51 bps), réversion funding (−8.7 bps), B6 décile taker (−8.7) ET maker (−0.5 : le mur de coût/spread est la contrainte, pas les params), D1 binaire (−4.3, sous le coût). Aucun n'est éligible au paper.
- **Provisoire données LOW** : les tests seconds (B6/D1) reposent sur ~4.6 j → verdict faible de toute façon ; même négatifs ils ne méritent pas de re-test.
- **Reco** : conserver UNIQUEMENT ZEC en paper (edge prouvé en prod, non touché ; mais on sait maintenant qu'il n'est NI généralisable NI significatif à 95 % OOS → surveiller l'AvgGross live, ne pas ajouter de capital). Prochaines pistes à tester proprement : CrossSectionalReversal, LiquidationCascadeReversal, ResidualBTCReversion (adaptateurs à écrire).

*Le mérite de ce pipeline n'est pas d'avoir trouvé un edge, mais d'avoir honnêtement REJETÉ des edges illusoires que le backtest naïf aurait validés.*