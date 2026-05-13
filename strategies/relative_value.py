"""
relative_value.py — Relative Value (statistical pairs-trading) strategy.

PAPER ONLY — never trade live without extended validation.

For each pair (A, B):
  - Compute log-price spread using rolling OLS: spread = log(P_A) - alpha - beta*log(P_B)
  - Standardise to z-score over zscore_lookback bars
  - Enter LONG A when z-score < -entry_z  (A cheap relative to B)
  - Exit when z-score > exit_z (spread normalised) or hard z-stop

Only LONG positions (no shorting the hedge leg) to keep implementation simple
on Hyperliquid perps. Minimum correlation guard prevents trading unstable pairs.
"""
import logging
import math
from dataclasses import dataclass
from typing import Optional

from execution.cost_filter import CostFilter
from indicators.technical import rolling_beta, zscore
from strategies.bar_aggregator import BarAggregator
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


@dataclass
class _PairState:
    leg_a:       str
    leg_b:       str
    agg_a:       BarAggregator
    agg_b:       BarAggregator
    beta:        Optional[float] = None
    alpha:       Optional[float] = None
    spread_hist: list = None          # rolling spread values
    z_score:     Optional[float] = None
    correlation: Optional[float] = None
    has_position: bool = False
    pos_id:       str  = ""
    entry_ts:     float = 0.0

    def __post_init__(self):
        if self.spread_hist is None:
            self.spread_hist = []


class RelativeValueStrategy(BaseStrategy):
    """
    Relative Value (pairs z-score) — experimental paper-only strategy.

    Config params:
        pairs                  list  [["ETH","BTC"],["SOL","ETH"],["SOL","BTC"]]
        regression_lookback    int   500   OLS regression window (1h bars)
        zscore_lookback        int   200   Z-score rolling window
        entry_z                float -2.0  Enter long leg A when z < entry_z
        exit_z                 float  0.0  Exit when z > exit_z
        stop_z                 float -3.5  Hard z-score stop
        min_correlation        float  0.70 Skip pair if |corr| < threshold
        stop_loss_pct          float  0.06 Hard price stop loss
        take_profit_pct        float  0.03 Take profit
        max_hold_hours         float  48   Max hold
        min_cost_ratio         float  3.0  CostFilter
    """

    def __init__(self, config: StrategyConfig, **kwargs):
        super().__init__(config, **kwargs)
        p = config.params

        raw_pairs = p.get("pairs", [["ETH", "BTC"], ["SOL", "ETH"]])
        self._reg_lookback  = int(p.get("regression_lookback", 500))
        self._zs_lookback   = int(p.get("zscore_lookback",     200))
        self._entry_z       = float(p.get("entry_z",           -2.0))
        self._exit_z        = float(p.get("exit_z",             0.0))
        self._stop_z        = float(p.get("stop_z",            -3.5))
        self._min_corr      = float(p.get("min_correlation",    0.70))
        self._sl_pct        = float(p.get("stop_loss_pct",      0.06))
        self._tp_pct        = float(p.get("take_profit_pct",    0.03))
        self._max_hold_s    = float(p.get("max_hold_hours",     48)) * 3600
        self._cost_filter      = CostFilter(min_ratio=float(p.get("min_cost_ratio", 3.0)))
        self._require_hedge    = bool(p.get("require_beta_hedge", True))

        # Build pair states (both legs needed in coins list)
        self._pairs: dict[str, _PairState] = {}   # key = "A/B"
        self._coin_to_pairs: dict[str, list[str]] = {}  # coin → [pair keys]

        for pair in raw_pairs:
            if len(pair) != 2:
                continue
            a, b = pair[0].upper(), pair[1].upper()
            key = f"{a}/{b}"
            self._pairs[key] = _PairState(
                leg_a = a, leg_b = b,
                agg_a = BarAggregator(a, 60, maxlen=max(self._reg_lookback + 50, 600)),
                agg_b = BarAggregator(b, 60, maxlen=max(self._reg_lookback + 50, 600)),
            )
            self._coin_to_pairs.setdefault(a, []).append(key)
            self._coin_to_pairs.setdefault(b, []).append(key)

        # Track which pair each open position belongs to
        self._pos_to_pair: dict[str, str] = {}   # pos_id → pair_key

    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        # Feed all relevant pair aggregators
        for key in self._coin_to_pairs.get(symbol, []):
            ps = self._pairs[key]
            if symbol == ps.leg_a:
                bar_a = ps.agg_a.update(bar)
                bar_b = None
            else:
                bar_b = ps.agg_b.update(bar)
                bar_a = None

            # Only recompute when both legs have a fresh 1h bar from leg_a perspective
            # (simplified: recompute after any new 1h bar from either leg)
            if bar_a is not None or bar_b is not None:
                self._update_pair_stats(ps)

        # Check signals for all pairs whose leg_a = symbol
        best_decision: Optional[StrategyDecision] = None
        for key, ps in self._pairs.items():
            if ps.leg_a != symbol:
                continue
            if ps.has_position:
                continue
            d = self._check_entry(key, ps, ts)
            if d is not None and d.action != "SKIP":
                best_decision = d
                break
        return best_decision

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        mid = book.mid if hasattr(book, "mid") else (book.best_bid + book.best_ask) / 2
        for key, ps in self._pairs.items():
            if ps.leg_a != symbol or not ps.has_position:
                continue
            # Z-score exit
            z = ps.z_score
            if z is not None:
                if z >= self._exit_z:
                    return StrategyDecision(action="CLOSE", symbol=symbol,
                                            reason=f"z_exit z={z:.3f}>{self._exit_z}",
                                            metadata={"pos_id": ps.pos_id, "pair": key})
                if z <= self._stop_z:
                    return StrategyDecision(action="CLOSE", symbol=symbol,
                                            reason=f"z_stop z={z:.3f}<={self._stop_z}",
                                            metadata={"pos_id": ps.pos_id, "pair": key})
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        for key, ps in self._pairs.items():
            if ps.leg_a == symbol and not ps.has_position:
                ps.has_position = True
                ps.pos_id       = pos_id
                ps.entry_ts     = ts
                self._pos_to_pair[pos_id] = key
                return {
                    "tp_price":         price * (1 + self._tp_pct),
                    "stop_price":       price * (1 - self._sl_pct),
                    "max_hold_seconds": int(self._max_hold_s),
                }
        return None

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        for ps in self._pairs.values():
            if ps.leg_a == symbol and ps.has_position:
                self._pos_to_pair.pop(ps.pos_id, None)
                ps.has_position = False
                ps.pos_id       = ""
                break
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        data = {}
        for key, ps in self._pairs.items():
            if ps.leg_a != symbol:
                continue
            data[key] = {
                "bars_1h_a":    len(ps.agg_a),
                "bars_1h_b":    len(ps.agg_b),
                "beta":         round(ps.beta,  4) if ps.beta  is not None else None,
                "alpha":        round(ps.alpha, 4) if ps.alpha is not None else None,
                "z_score":      round(ps.z_score, 3) if ps.z_score is not None else None,
                "correlation":  round(ps.correlation, 3) if ps.correlation is not None else None,
                "has_position": ps.has_position,
                "hedge_required": self._require_hedge,
                "executable":     not self._require_hedge,
            }
        return {"pairs": data} if data else {}

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _update_pair_stats(self, ps: _PairState) -> None:
        closes_a = ps.agg_a.closes()
        closes_b = ps.agg_b.closes()
        n_min    = min(len(closes_a), len(closes_b))
        if n_min < self._zs_lookback + 10:
            return

        log_a = [math.log(c) for c in closes_a[-n_min:]]
        log_b = [math.log(c) for c in closes_b[-n_min:]]

        # Rolling OLS beta over regression_lookback
        lookback = min(self._reg_lookback, n_min)
        res = rolling_beta(log_a, log_b, lookback)
        if res is None:
            return
        ps.beta, ps.alpha = res

        # Spread series
        spread = [log_a[i] - ps.alpha - ps.beta * log_b[i]
                  for i in range(len(log_a))]
        ps.spread_hist = spread

        # Z-score
        zs = zscore(spread, self._zs_lookback)
        ps.z_score = zs

        # Pearson correlation over regression_lookback
        sub_a = log_a[-lookback:]
        sub_b = log_b[-lookback:]
        mu_a  = sum(sub_a) / lookback
        mu_b  = sum(sub_b) / lookback
        cov   = sum((sub_a[i] - mu_a) * (sub_b[i] - mu_b) for i in range(lookback))
        var_a = sum((v - mu_a) ** 2 for v in sub_a)
        var_b = sum((v - mu_b) ** 2 for v in sub_b)
        denom = math.sqrt(var_a * var_b) if var_a * var_b > 0 else 0
        ps.correlation = cov / denom if denom else 0.0

    def _check_entry(self, key: str, ps: _PairState,
                     ts: float) -> Optional[StrategyDecision]:
        symbol = ps.leg_a

        if ps.z_score is None or ps.correlation is None:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"rv_warmup pair={key}")

        if abs(ps.correlation) < self._min_corr:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"rv_low_corr corr={ps.correlation:.3f}<{self._min_corr}")

        if ps.z_score >= self._entry_z:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"rv_no_signal z={ps.z_score:.3f}>={self._entry_z}")

        # Cost filter
        expected_bps = self._tp_pct * 10000
        ok, cost_reason, _ = self._cost_filter.is_worth_taking(expected_bps)
        if not ok:
            return StrategyDecision(action="SKIP", symbol=symbol, reason=cost_reason)

        # require_beta_hedge=True → scanner only (no hedge leg on Hyperliquid perps)
        if self._require_hedge:
            return StrategyDecision(
                action="SKIP", symbol=symbol,
                reason=f"rv_scanner_only pair={key} z={ps.z_score:.3f} hedge_required=True",
                metadata={"pair": key, "z_score": ps.z_score, "beta": ps.beta,
                          "hedge_required": True, "executable": False},
            )

        notional = min(
            self.config.capital_allocated_usd / max(len(self._pairs), 1),
            self.config.max_position_size_usd,
        )
        return StrategyDecision(
            action="PLACE_BUY",
            symbol=symbol,
            reason=f"rv_entry pair={key} z={ps.z_score:.3f} corr={ps.correlation:.3f}",
            notional_usd=notional,
            stop_loss=None,
            take_profit=None,
            max_hold_seconds=int(self._max_hold_s),
            metadata={"pair": key, "z_score": ps.z_score, "beta": ps.beta},
        )
