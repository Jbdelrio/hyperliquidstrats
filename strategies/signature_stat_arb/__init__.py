"""Signature-based statistical-arbitrage strategy (independent, backtest-only).

Inspired by "Signature-Based Optimal Execution for Statistical Arbitrage with
Path-Dependent Trading Signals". Two explicitly separated layers:

    Layer 1 (alpha)     : path signature of an information path -> expected edge.
    Layer 2 (execution) : linear speed policy v_t = B x_t solved from a convex
                          empirical objective (edge vs execution cost, inventory
                          risk, dollar-neutrality and terminal liquidation).

SECURITY: this package never sends live orders. ``StrategyConfig.backtest_only``
defaults to True and there is no live-trading code path here (§32).
"""
from .config import (StrategyConfig, DataConfig, SpreadConfig, SignatureConfig,
                     AlphaConfig, OptimizerConfig, ExecutionConfig, CostConfig,
                     RiskConfig, WalkForwardConfig, PRESETS, ALL_CHANNELS, FREQS)
from .backtest import run_backtest
from .data_loader import load_pair, PairData

BACKTEST_ONLY = True
NAME = "signature_stat_arb"

__all__ = ["StrategyConfig", "DataConfig", "SpreadConfig", "SignatureConfig",
           "AlphaConfig", "OptimizerConfig", "ExecutionConfig", "CostConfig",
           "RiskConfig", "WalkForwardConfig", "PRESETS", "ALL_CHANNELS", "FREQS",
           "run_backtest", "load_pair", "PairData", "BACKTEST_ONLY", "NAME"]
