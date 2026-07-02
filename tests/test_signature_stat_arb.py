"""Tests for the signature stat-arb strategy (§30).

Focus on the properties that guard correctness and the no-look-ahead discipline.
Run: python -m pytest tests/test_signature_stat_arb.py -q
"""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from strategies.signature_stat_arb import (StrategyConfig, run_backtest)
from strategies.signature_stat_arb.config import SignatureConfig, ConfigError
from strategies.signature_stat_arb.signature_features import (
    signature, signature_dimension, signature_feature_names, levy_area,
    IncrementalSignature)
from strategies.signature_stat_arb.execution_optimizer import (
    fit_policy, simulate_inventory)
from strategies.signature_stat_arb.config import OptimizerConfig, RiskConfig
from strategies.signature_stat_arb.risk_manager import RiskManager
from strategies.signature_stat_arb.walk_forward import make_folds, single_split
from strategies.signature_stat_arb.config import WalkForwardConfig
from strategies.signature_stat_arb.alpha_model import AlphaModel
from strategies.signature_stat_arb.two_leg_execution import TwoLegExecutionManager
from strategies.signature_stat_arb.costs import CostModel
from strategies.signature_stat_arb.fill_models import FillModel
from strategies.signature_stat_arb.config import CostConfig, ExecutionConfig, AlphaConfig
from strategies.signature_stat_arb import backtest as BT


# ----------------------------------------------------------------- signatures
def test_signature_dimension_matches_config():
    for d in range(1, 6):
        for depth in (1, 2, 3):
            assert SignatureConfig(depth=depth, channels=None or _chans(d)).dimension \
                == signature_dimension(d, depth)


def _chans(d):
    from strategies.signature_stat_arb.config import ALL_CHANNELS
    return ALL_CHANNELS[:d]


def test_feature_names_length_equals_dimension():
    ch = _chans(4)
    names = signature_feature_names(ch, 2)
    assert len(names) == signature_dimension(4, 2)


def test_levy_area_sign_flips_when_channels_swapped():
    rng = np.random.default_rng(0)
    path = np.cumsum(rng.standard_normal((50, 3)) * 0.1, axis=0)
    A = levy_area(path)
    Aswap = levy_area(path[:, [1, 0, 2]])
    assert np.allclose(A, -A.T)                       # antisymmetric
    assert np.isclose(A[0, 1], -Aswap[0, 1])          # swap flips sign


def test_incremental_signature_equals_batch():
    rng = np.random.default_rng(1)
    path = np.cumsum(rng.standard_normal((40, 3)) * 0.1, axis=0)
    inc = IncrementalSignature(2, 3)
    for p in path:
        inc.append(p)
    assert np.allclose(inc.value(), signature(path, 2))


# ----------------------------------------------------------------- optimizer
def _toy_opt():
    rng = np.random.default_rng(0)
    T, m = 400, 5
    X = rng.standard_normal((T, m)); X[:, 0] = 1.0
    alpha = 0.5 * X[:, 1]
    sigma = np.abs(rng.standard_normal(T)) * 0.1 + 0.5
    net = np.full(T, 0.3)
    return X, alpha, sigma, net, np.ones(T, bool)


def test_rho_reduces_policy_norm():
    X, a, s, n, tr = _toy_opt()
    lo = fit_policy(X, a, s, n, OptimizerConfig(execution_ridge_rho=0.1), 1.0, 60, tr)
    hi = fit_policy(X, a, s, n, OptimizerConfig(execution_ridge_rho=100.0), 1.0, 60, tr)
    assert np.linalg.norm(hi.theta_raw) < np.linalg.norm(lo.theta_raw)


def test_gamma_reduces_terminal_inventory():
    X, a, s, n, tr = _toy_opt()
    def term(g):
        p = fit_policy(X, a, s, n, OptimizerConfig(terminal_penalty_gamma=g), 1.0, 50, tr)
        Q, _ = simulate_inventory(X, p.theta, OptimizerConfig(terminal_penalty_gamma=g), 1.0, 50)
        return np.mean([abs(Q[i]) for i in range(48, len(Q), 50)])
    assert term(50.0) <= term(0.1) + 1e-9


def test_phi_reduces_average_inventory():
    X, a, s, n, tr = _toy_opt()
    def avg(phi):
        c = OptimizerConfig(inventory_risk_phi=phi)
        p = fit_policy(X, a, s, n, c, 1.0, 60, tr)
        Q, _ = simulate_inventory(X, p.theta, c, 1.0, 60)
        return np.mean(np.abs(Q))
    assert avg(20.0) <= avg(0.05)


def test_no_trade_band_reduces_orders():
    X, a, s, n, tr = _toy_opt()
    c = OptimizerConfig()
    p = fit_policy(X, a, s, n, c, 1.0, 60, tr, scale_to_utilization=0.5)
    _, v0 = simulate_inventory(X, p.theta, c, 1.0, 60, no_trade_band=0.0)
    _, vb = simulate_inventory(X, p.theta, c, 1.0, 60, no_trade_band=0.05)
    assert np.sum(vb != 0) <= np.sum(v0 != 0)


def test_terminal_liquidation_flat_at_episode_end():
    X, a, s, n, tr = _toy_opt()
    c = OptimizerConfig()
    p = fit_policy(X, a, s, n, c, 1.0, 50, tr, scale_to_utilization=0.5)
    Q, _ = simulate_inventory(X, p.theta, c, 1.0, 50)
    ends = [Q[i] for i in range(49, len(Q), 50)]
    assert np.allclose(ends, 0.0)


def test_solution_uses_no_explicit_inverse_and_is_reproducible():
    X, a, s, n, tr = _toy_opt()
    p1 = fit_policy(X, a, s, n, OptimizerConfig(), 1.0, 60, tr)
    p2 = fit_policy(X, a, s, n, OptimizerConfig(), 1.0, 60, tr)
    assert np.allclose(p1.theta, p2.theta)


# ----------------------------------------------------------------- risk
def test_position_respects_limits():
    rm = RiskManager(RiskConfig(maximum_position_per_leg=75, maximum_gross_exposure=150,
                                maximum_net_exposure=15, maximum_leverage=1.5,
                                initial_capital=300))
    for q in np.linspace(-2, 2, 21):
        for beta in (0.5, 1.0, 2.0):
            t = rm.size_target(q, beta)
            assert abs(t) <= 75 + 1e-6
            assert abs(t) * (1 + abs(beta)) <= 150 + 1e-6


def test_kill_switch_halts_on_drawdown():
    rm = RiskManager(RiskConfig(initial_capital=1000, maximum_drawdown_pct=8))
    rm.update_equity(1000, 0)
    rm.update_equity(900, 10)          # -10% from peak
    assert rm.state.halted
    assert rm.size_target(1.0, 1.0) == 0.0


# ----------------------------------------------------------------- walk-forward
def test_walk_forward_no_lookahead_and_ordered():
    ts = np.arange(0, 120 * 86400, 3600, dtype=float)   # 120 days hourly
    folds = make_folds(ts, WalkForwardConfig(train_days=30, validation_days=7,
                                             test_days=7, step_days=7,
                                             purge_seconds=3600, embargo_seconds=1800),
                       label_horizon_seconds=600)
    assert len(folds) > 0
    for f in folds:
        assert ts[f.train].max() < ts[f.test].min()     # train strictly before test
        assert ts[f.validation].max() < ts[f.test].min()
        # no index overlap
        assert not (set(f.train.tolist()) & set(f.test.tolist()))


def test_single_split_purges_label_overlap():
    ts = np.arange(0, 10000, 1.0)
    f = single_split(ts, 0.6, 0.2, label_horizon_seconds=100, purge_seconds=0)
    # last train ts must be at least horizon before val start
    val_start = ts[f.validation[0]]
    assert ts[f.train].max() <= val_start - 100


# ----------------------------------------------------------------- alpha
def test_alpha_target_has_no_future_leak():
    s = np.arange(100.0)
    y = AlphaModel.make_target_neg_spread_change(s, 5)
    assert np.isnan(y[-5:]).all()                        # last h rows undefined
    assert np.isclose(y[0], -(s[5] - s[0]))


# ----------------------------------------------------------------- two-leg
def test_two_leg_manager_tracks_hedge():
    cm = CostModel(CostConfig()); fm = FillModel(ExecutionConfig(fill_model="bid_ask"))
    mgr = TwoLegExecutionManager(cm, fm)
    info = mgr.rebalance(100.0, 1.0, 100.0, 50.0)
    assert np.isclose(mgr.leg1.notional, 100.0)
    assert np.isclose(mgr.leg2.notional, -100.0)         # -beta*target
    assert abs(info["dollar_imbalance"]) < 1e-6          # dollar-neutral at beta=1


# ----------------------------------------------------------------- config
def test_config_validation_rejects_bad_values():
    c = StrategyConfig()
    c.signature.depth = 4
    with pytest.raises(ConfigError):
        c.validate()
    c2 = StrategyConfig()
    c2.data.decision_frequency = "1s"; c2.data.market_data_frequency = "5s"
    with pytest.raises(ConfigError):
        c2.validate()


def test_config_roundtrip():
    c = StrategyConfig()
    d = c.to_dict()
    c2 = StrategyConfig.from_dict(d)
    assert c2.to_dict() == d


# ----------------------------------------------------------------- integration
@pytest.fixture(scope="module")
def demo_result():
    cfg = StrategyConfig()
    cfg.data.source = "demo"
    cfg.data.market_data_frequency = "30s"
    cfg.data.decision_frequency = "30s"
    cfg.data.start = "2024-01-01"; cfg.data.end = "2024-01-06"
    return run_backtest(cfg, max_bars=20000)


def test_backtest_runs_and_is_flagged_demo(demo_result):
    assert demo_result["is_demo"] is True
    assert "metrics" in demo_result and "benchmarks" in demo_result


def test_costs_reduce_pnl():
    """Same run, higher fees -> lower net PnL (costs are actually applied)."""
    def net(fee):
        cfg = StrategyConfig()
        cfg.data.source = "demo"; cfg.data.market_data_frequency = "30s"
        cfg.data.decision_frequency = "30s"
        cfg.data.start = "2024-01-01"; cfg.data.end = "2024-01-06"
        cfg.costs.taker_fee_bps = fee; cfg.costs.maker_fee_bps = fee
        return run_backtest(cfg, max_bars=20000)["metrics"]["net_pnl"]
    assert net(20.0) < net(0.0) + 1e-9


def test_dimension_reported_matches(demo_result):
    sig = demo_result["signature"]
    assert sig["dimension"] == signature_dimension(sig["n_channels"], sig["depth"])
    assert len(sig["feature_names"]) == sig["dimension"]
