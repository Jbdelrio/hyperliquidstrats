"""Configuration objects for the signature-based statistical-arbitrage strategy.

One config object per concern (§28). Every parameter has a type, a default, a
short description and (where relevant) bounds enforced by ``validate()``. The
whole tree serialises to/from plain dicts (YAML/JSON) with no code dependency.

SECURITY: ``backtest_only`` defaults to True and there is *no* live-order code
path in this package. Turning it off does nothing on its own — it only exists so
a future, explicit live integration can gate on it.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from typing import List, Optional, Dict, Any


class ConfigError(ValueError):
    """Raised when a configuration value is out of its documented bounds."""


def _check(cond: bool, msg: str):
    if not cond:
        raise ConfigError(msg)


# --------------------------------------------------------------------------- #
#  Data
# --------------------------------------------------------------------------- #
@dataclass
class DataConfig:
    exchange: str = "binance"                 # binance | bybit | hyperliquid | file
    symbol_1: str = "BTC"                     # leg 1 (log P1)
    symbol_2: str = "ETH"                     # leg 2 (log P2)
    factor_symbols: List[str] = field(default_factory=lambda: ["BTC", "ETH"])  # 5.5 residual
    quote: str = "USDT"
    start: str = "2024-01-01"
    end: str = "2024-03-01"
    market_data_frequency: str = "5s"         # 1s|5s|10s|15s|30s|1min|5min
    decision_frequency: str = "10s"           # how often the policy re-decides
    source: str = "auto"                      # auto | file | binance | demo
    file_1: Optional[str] = None              # parquet/csv path for leg 1 (source=file)
    file_2: Optional[str] = None
    timezone: str = "UTC"
    gap_fill: str = "ffill"                   # ffill | drop | none
    max_gap_seconds: int = 120                # gaps larger than this are NOT ffilled

    def validate(self):
        _check(self.market_data_frequency in FREQS, f"market_data_frequency must be one of {FREQS}")
        _check(self.decision_frequency in FREQS, f"decision_frequency must be one of {FREQS}")
        _check(freq_seconds(self.decision_frequency) >= freq_seconds(self.market_data_frequency),
               "decision_frequency must be >= market_data_frequency")
        _check(self.gap_fill in ("ffill", "drop", "none"), "gap_fill invalid")


# --------------------------------------------------------------------------- #
#  Spread
# --------------------------------------------------------------------------- #
@dataclass
class SpreadConfig:
    method: str = "kalman"                    # ratio | ols | ridge | kalman | factor
    window_seconds: int = 8 * 3600            # rolling estimation window (ols/ridge/stats)
    zscore_window_seconds: int = 8 * 3600
    ridge_lambda: float = 1.0                 # for method=ridge / factor
    static_beta: float = 1.0                  # for method=ratio
    kalman_process_variance: float = 1.0e-5   # Q
    kalman_observation_variance: float = 1.0e-3  # R
    kalman_beta_init: float = 1.0
    kalman_alpha_init: float = 0.0
    kalman_init_cov: float = 1.0
    hedge_ratio_min: float = -10.0
    hedge_ratio_max: float = 10.0
    # regime gates (positions disabled when violated) -----------------------
    adf_pvalue_max: float = 0.10              # require stationarity below this
    min_halflife_bars: float = 2.0
    max_halflife_bars: float = 5000.0
    hedge_stability_max: float = 0.5          # rolling std(beta)/|mean(beta)| ceiling
    max_zscore_abs: float = 8.0               # |z| above this = regime break -> flat

    def validate(self):
        _check(self.method in ("ratio", "ols", "ridge", "kalman", "factor"), "spread.method invalid")
        _check(self.window_seconds > 0 and self.zscore_window_seconds > 0, "windows must be > 0")
        _check(self.ridge_lambda >= 0, "ridge_lambda >= 0")
        _check(self.hedge_ratio_min < self.hedge_ratio_max, "hedge_ratio_min < max")
        _check(0 < self.adf_pvalue_max <= 1, "adf_pvalue_max in (0,1]")


# --------------------------------------------------------------------------- #
#  Signature
# --------------------------------------------------------------------------- #
# Channels available for the information path Z_t (§7). Order matters and is the
# order used to build the path matrix and the signature term names.
ALL_CHANNELS = [
    "normalized_time",   # tau = t/T  (must be first for time-augmentation intuition)
    "spread",            # s_t
    "zscore",            # z_t
    "asset_1_return",    # r1
    "asset_2_return",    # r2
    "btc_return",        # market factor
    "order_flow_imbalance",
    "realized_volatility",
    "book_spread",       # bid-ask
    "funding",
]


@dataclass
class SignatureConfig:
    depth: int = 2                            # 1 | 2 | 3 (3 warns)
    window_seconds: int = 30 * 60            # rolling path window
    channels: List[str] = field(default_factory=lambda: [
        "normalized_time", "spread", "zscore",
        "asset_1_return", "asset_2_return", "realized_volatility"])
    normalization: str = "rolling_robust"     # rolling_zscore|rolling_robust|rolling_minmax|none|train_fit
    include_levy: bool = True                 # expose antisymmetric level-2 terms
    use_iisignature: bool = True              # use lib if importable, else numpy fallback

    def validate(self):
        _check(self.depth in (1, 2, 3), "signature.depth must be 1, 2 or 3")
        _check(len(self.channels) >= 1, "at least one channel")
        for c in self.channels:
            _check(c in ALL_CHANNELS, f"unknown channel {c}")
        _check(self.normalization in (
            "rolling_zscore", "rolling_robust", "rolling_minmax", "none", "train_fit"),
            "normalization invalid")

    @property
    def n_channels(self) -> int:
        return len(self.channels)

    @property
    def dimension(self) -> int:
        """m = sum_{k=0}^{N} d^k  (includes the constant level-0 term)."""
        d = self.n_channels
        return sum(d ** k for k in range(self.depth + 1))


# --------------------------------------------------------------------------- #
#  Alpha
# --------------------------------------------------------------------------- #
@dataclass
class AlphaConfig:
    method: str = "ridge"                     # zscore | ridge | elasticnet
    horizon_seconds: int = 10 * 60            # prediction horizon h
    ridge_penalty: float = 1.0
    l1_ratio: float = 0.5                     # elasticnet only
    zscore_gain: float = 1.0                  # Mode A: alpha = -gain * z
    target: str = "neg_spread_change"         # neg_spread_change | hedged_pnl
    min_confidence: float = 0.0               # |alpha| below this -> treated as 0

    def validate(self):
        _check(self.method in ("zscore", "ridge", "elasticnet"), "alpha.method invalid")
        _check(self.horizon_seconds > 0, "horizon > 0")
        _check(self.ridge_penalty >= 0, "ridge_penalty >= 0")
        _check(self.target in ("neg_spread_change", "hedged_pnl"), "alpha.target invalid")


# --------------------------------------------------------------------------- #
#  Execution optimizer (v_t = B x_t)
# --------------------------------------------------------------------------- #
@dataclass
class OptimizerConfig:
    execution_ridge_rho: float = 1.0          # -rho||B||_F^2 regulariser
    inventory_risk_phi: float = 1.0           # phi Q'ΣQ
    dollar_neutrality_eta: float = 5.0        # eta (Q'P)^2
    terminal_penalty_gamma: float = 10.0      # gamma |Q_T|^2
    execution_cost_lambda: float = 1.0        # v'Λv (Λ = lambda * I here)
    maximum_velocity: float = 0.10            # cap on |v_t| (fraction of cap per step)
    inventory_cap: float = 1.0                # |Q| cap (fraction of max_position)
    policy_scale_target: float = 0.5          # target p95(|Q|)/cap (0 = raw solved policy)

    def validate(self):
        for k in ("execution_ridge_rho", "inventory_risk_phi", "dollar_neutrality_eta",
                  "terminal_penalty_gamma", "execution_cost_lambda"):
            _check(getattr(self, k) >= 0, f"{k} >= 0")
        _check(self.maximum_velocity > 0, "maximum_velocity > 0")
        _check(self.inventory_cap > 0, "inventory_cap > 0")


# --------------------------------------------------------------------------- #
#  Execution (fills / costs routing)
# --------------------------------------------------------------------------- #
@dataclass
class ExecutionConfig:
    mode: str = "maker_first"                 # maker_only | taker_only | maker_first
    fill_model: str = "bid_ask"               # simple | bid_ask | maker_prob
    no_trade_band_usd: float = 10.0           # dead-band on |target-actual| notional
    minimum_order_notional: float = 10.0
    maker_timeout_seconds: int = 10
    emergency_exit_mode: str = "taker"
    simulated_latency_ms: int = 250
    max_orders_per_minute: int = 30

    def validate(self):
        _check(self.mode in ("maker_only", "taker_only", "maker_first"), "execution.mode invalid")
        _check(self.fill_model in ("simple", "bid_ask", "maker_prob"), "fill_model invalid")
        _check(self.no_trade_band_usd >= 0, "no_trade_band >= 0")


# --------------------------------------------------------------------------- #
#  Costs (per-exchange overridable) — never hard-code "current" fees (§13)
# --------------------------------------------------------------------------- #
@dataclass
class CostConfig:
    maker_fee_bps: float = 1.5
    taker_fee_bps: float = 4.5
    default_slippage_bps: float = 1.0
    temporary_impact_coefficient: float = 0.0  # b in C(v)=a|v|+b v^2 + c·1{v!=0}
    fixed_cost_per_trade_usd: float = 0.0      # c
    funding_bps_per_interval: float = 0.0      # applied on held notional per funding interval
    latency_penalty_bps: float = 0.0

    def validate(self):
        for k in ("maker_fee_bps", "taker_fee_bps", "default_slippage_bps"):
            _check(getattr(self, k) >= 0, f"{k} >= 0")


# --------------------------------------------------------------------------- #
#  Risk
# --------------------------------------------------------------------------- #
@dataclass
class RiskConfig:
    initial_capital: float = 1000.0
    maximum_gross_exposure: float = 500.0
    maximum_net_exposure: float = 50.0
    maximum_leverage: float = 2.0
    maximum_position_per_leg: float = 250.0
    cash_buffer_ratio: float = 0.35
    maximum_holding_period_seconds: int = 60 * 60
    maximum_daily_loss_pct: float = 2.0
    maximum_drawdown_pct: float = 8.0
    maximum_loss_per_trade_pct: float = 1.0
    warmup_seconds: int = 8 * 3600
    kill_switch: bool = True

    def validate(self):
        _check(self.initial_capital > 0, "initial_capital > 0")
        _check(self.maximum_gross_exposure > 0, "max_gross > 0")
        _check(0 <= self.cash_buffer_ratio < 1, "cash_buffer in [0,1)")
        _check(self.maximum_position_per_leg > 0, "max_position_per_leg > 0")


# --------------------------------------------------------------------------- #
#  Walk-forward
# --------------------------------------------------------------------------- #
@dataclass
class WalkForwardConfig:
    scheme: str = "walk_forward"              # fixed | expanding | rolling | walk_forward
    train_days: int = 30
    validation_days: int = 7
    test_days: int = 7
    step_days: int = 7
    purge_seconds: int = 60 * 60              # drop obs overlapping the future horizon
    embargo_seconds: int = 30 * 60

    def validate(self):
        _check(self.scheme in ("fixed", "expanding", "rolling", "walk_forward"), "wf.scheme invalid")
        for k in ("train_days", "test_days"):
            _check(getattr(self, k) > 0, f"{k} > 0")
        _check(self.purge_seconds >= 0 and self.embargo_seconds >= 0, "purge/embargo >= 0")


# --------------------------------------------------------------------------- #
#  Top-level
# --------------------------------------------------------------------------- #
@dataclass
class StrategyConfig:
    name: str = "signature_stat_arb"
    backtest_only: bool = True                # SECURITY: never send live orders (§32)
    seed: int = 0
    data: DataConfig = field(default_factory=DataConfig)
    spread: SpreadConfig = field(default_factory=SpreadConfig)
    signature: SignatureConfig = field(default_factory=SignatureConfig)
    alpha: AlphaConfig = field(default_factory=AlphaConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    costs: CostConfig = field(default_factory=CostConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    walk_forward: WalkForwardConfig = field(default_factory=WalkForwardConfig)

    def validate(self) -> "StrategyConfig":
        for sub in (self.data, self.spread, self.signature, self.alpha, self.optimizer,
                    self.execution, self.costs, self.risk, self.walk_forward):
            sub.validate()
        # cross-object sanity
        _check(self.risk.maximum_net_exposure <= self.risk.maximum_gross_exposure,
               "net exposure cannot exceed gross exposure")
        if self.signature.depth >= 3:
            # not an error, but callers should surface the warning in the UI
            pass
        return self

    # ---- (de)serialisation ------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "StrategyConfig":
        d = dict(d or {})
        sub_map = {
            "data": DataConfig, "spread": SpreadConfig, "signature": SignatureConfig,
            "alpha": AlphaConfig, "optimizer": OptimizerConfig, "execution": ExecutionConfig,
            "costs": CostConfig, "risk": RiskConfig, "walk_forward": WalkForwardConfig,
        }
        kwargs: Dict[str, Any] = {}
        for k in ("name", "backtest_only", "seed"):
            if k in d:
                kwargs[k] = d[k]
        for key, klass in sub_map.items():
            if key in d and isinstance(d[key], dict):
                valid = {f.name for f in fields(klass)}
                kwargs[key] = klass(**{k: v for k, v in d[key].items() if k in valid})
        return cls(**kwargs)


# --------------------------------------------------------------------------- #
#  Presets (§16)
# --------------------------------------------------------------------------- #
def preset_prudent_300() -> StrategyConfig:
    c = StrategyConfig()
    c.risk = RiskConfig(initial_capital=300, maximum_gross_exposure=150,
                        maximum_net_exposure=15, maximum_leverage=1.5,
                        maximum_position_per_leg=75, cash_buffer_ratio=0.40)
    return c


def preset_prudent_1000() -> StrategyConfig:
    c = StrategyConfig()
    c.risk = RiskConfig(initial_capital=1000, maximum_gross_exposure=500,
                        maximum_net_exposure=50, maximum_leverage=2.0,
                        maximum_position_per_leg=250, cash_buffer_ratio=0.35)
    return c


PRESETS = {"prudent_300": preset_prudent_300, "prudent_1000": preset_prudent_1000}


# --------------------------------------------------------------------------- #
#  Frequency helpers
# --------------------------------------------------------------------------- #
FREQS = ["1s", "5s", "10s", "15s", "30s", "1min", "5min"]
_FREQ_SECONDS = {"1s": 1, "5s": 5, "10s": 10, "15s": 15, "30s": 30, "1min": 60, "5min": 300}


def freq_seconds(f: str) -> int:
    if f not in _FREQ_SECONDS:
        raise ConfigError(f"unknown frequency {f}")
    return _FREQ_SECONDS[f]
