"""
test_walkforward.py — PHASE 2 : harnais anti-overfit.

Vérifie le découpage walk-forward, le filtre OOS+embargo, le Deflated Sharpe
(pénalité multiple-testing), la détection de plateau vs pic isolé, et un
end-to-end GO/NO-GO sur run_fn synthétiques.
"""
from __future__ import annotations

import numpy as np

from backtesting import walkforward as W


def test_fold_bounds_cover_range():
    ts = list(np.linspace(1000, 2000, 100))
    b = W.fold_bounds(ts, 5)
    assert len(b) == 5
    assert b[0][0] == 1000 and b[-1][1] == 2000
    # contigus
    for i in range(4):
        assert b[i][1] == b[i + 1][0]


def test_oos_filter_skips_first_block_and_embargo():
    # 1 trade par bloc, entrée au tout début du bloc
    bounds = [(0, 100), (100, 200), (200, 300), (300, 400)]
    # entrées : 50 (bloc0=train, toujours exclu), 105 (début bloc1 = zone embargo),
    #           160 (bloc1, cœur), 250 (bloc2, cœur)
    trades = [{"ts": e + 1, "hold_s": 1, "net": 1, "notional": 100}
              for e in (50, 105, 160, 250)]
    oos = W.oos_filter(trades, bounds, embargo_frac=0.0)
    assert len(oos) == 3                       # 50 exclu (train) ; 105,160,250 gardés
    # embargo 20% (bloc de 100 → fenêtre [a+20, b]) : 105 retiré (dans [100,120))
    oos2 = W.oos_filter(trades, bounds, embargo_frac=0.2)
    assert len(oos2) == 2                       # restent 160, 250


def test_deflated_sharpe_penalises_trials():
    rng = np.random.default_rng(0)
    rets = list(rng.normal(0.5, 1.0, 200))      # SR ~0.5, signal réel
    dsr_1, sr_1, sr0_1 = W.deflated_sharpe(rets, n_trials=1, sr_trials_std=0.0)
    dsr_many, _, sr0_many = W.deflated_sharpe(rets, n_trials=200, sr_trials_std=0.3)
    assert 0.0 <= dsr_many <= dsr_1 <= 1.0       # plus d'essais ⇒ DSR plus bas
    assert sr0_many > sr0_1                       # seuil relevé par le multiple-testing


def test_detect_plateau_vs_spike():
    axes = ["a"]
    # plateau : voisins du meilleur (a=3) aussi bons
    plateau_grid = {(1,): 12.0, (2,): 16.0, (3,): 20.0, (4,): 16.0, (5,): 12.0}
    ok, _ = W.detect_plateau(plateau_grid, (3,), axes)
    assert ok is True
    # pic isolé : voisins négatifs
    spike_grid = {(1,): -5.0, (2,): -5.0, (3,): 30.0, (4,): -5.0, (5,): -5.0}
    bad, _ = W.detect_plateau(spike_grid, (3,), axes)
    assert bad is False


def _make_run_fn(edge_fn, n_per_coin=48, noise=1.5, notional=1000.0):
    t0, span = 1_700_000_000.0, 300 * 86400.0

    def run_fn(params, coin, fee_bps, slip_bps):
        cost_rt = 2.0 * (fee_bps + slip_bps)
        edge = edge_fn(params)
        rng = np.random.default_rng(abs(hash((coin, params["a"]))) % (2**32))
        out = []
        for i in range(n_per_coin):
            entry = t0 + span * i / n_per_coin
            net_bps = edge - cost_rt + rng.normal(0.0, noise)
            out.append({"ts": entry + 3600.0, "hold_s": 3600.0,
                        "net": notional * net_bps / 1e4, "notional": notional})
        return out
    return run_fn


def test_e2e_go_on_robust_plateau():
    coins = ["C1", "C2", "C3", "C4"]
    grid = [{"a": a} for a in (1, 2, 3, 4, 5)]
    # edge en plateau autour de a=3, large marge vs coût (14 bps)
    run_fn = _make_run_fn(lambda p: 24.0 - 3.0 * abs(p["a"] - 3))
    v = W.evaluate_strategy("UTEST_GO", run_fn, coins, grid, ["a"],
                            interval="1h", confidence="HIGH",
                            min_coins=3, write_report=False)
    assert v.best_params["a"] == 3
    assert v.oos_avg_net_bps > 0
    assert v.plateau is True
    assert v.breadth_pos >= 3
    assert v.stress[15.0] > 0          # survit au stress 15 bps
    assert v.go is True


def test_e2e_nogo_on_isolated_spike():
    coins = ["C1", "C2", "C3", "C4"]
    grid = [{"a": a} for a in (1, 2, 3, 4, 5)]
    # un seul point gagnant (a=3), voisins perdants → pic isolé
    run_fn = _make_run_fn(lambda p: 40.0 if p["a"] == 3 else 2.0)
    v = W.evaluate_strategy("UTEST_NOGO", run_fn, coins, grid, ["a"],
                            interval="1h", confidence="HIGH",
                            min_coins=3, write_report=False)
    assert v.plateau is False
    assert v.go is False
    assert any("plateau" in r for r in v.reasons)
