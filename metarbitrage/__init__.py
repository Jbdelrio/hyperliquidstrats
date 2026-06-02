"""
metarbitrage — moniteur de spread cross-venues (lecture seule).

⚠️ ÉTAT : MESURE UNIQUEMENT. Aucune clé privée, aucun trade réel. Le but est de
MESURER si un écart de prix NET de coûts (frais + slippage) existe et persiste
entre venues, AVANT d'envisager toute exécution. Tant que le moniteur n'a pas
démontré un edge net positif et persistant, aucune exécution n'est implémentée
(cf. discipline anti-overfit du projet : on ne risque pas de capital sur un edge
non prouvé).
"""
