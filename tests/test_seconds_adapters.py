"""
test_seconds_adapters.py — PHASE 3 : adaptateurs run_fn secondes (gardés sur cache).

Vérifie la forme des trades et la cohérence maker < taker en coût (le maker doit
produire un net ≥ taker sur les mêmes entrées, puisqu'il paie moins de spread).
Skip si la parquet seconds n'est pas présente (pas de dépendance CI).
"""
from __future__ import annotations

import pytest

from backtesting import seconds_adapters as S


pytestmark = pytest.mark.skipif(not S.PARQUET.exists(),
                                reason="seconds_features.parquet absent")


def test_decile_run_fn_shape_and_maker_cheaper():
    params = {"horizon_s": 300, "decile": 0.10}
    taker = S.decile_run_fn("liquidity_vacuum", "long", maker=False)
    maker = S.decile_run_fn("liquidity_vacuum", "long", maker=True)
    tr_t = taker(params, "WLD", fee_bps=3.5, slip_bps=3.5)
    tr_m = maker(params, "WLD", fee_bps=3.5, slip_bps=3.5)
    if not tr_t:
        pytest.skip("pas assez de données WLD")
    # forme
    for t in tr_t[:5]:
        assert {"ts", "hold_s", "net", "notional"} <= set(t)
        assert t["notional"] > 0 and t["hold_s"] > 0
    # mêmes entrées (même signal) → même nombre de trades
    assert len(tr_t) == len(tr_m)
    # le maker paie moins de coût (capture le spread) ⇒ net agrégé maker > taker
    assert sum(t["net"] for t in tr_m) > sum(t["net"] for t in tr_t)
