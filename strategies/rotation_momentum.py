"""
rotation_momentum.py — Cross-sectional Rotation Momentum strategy.

Every `rebalance_minutes` minutes (default 60), all coins are ranked by
their N-bar log-return momentum. The top-K are buy candidates, bottom-K
are sell candidates.

Phase 1 — scanner/filter mode (default):
  No autonomous trades. Exposes get_top_coins() / get_bottom_coins() for
  other strategies to optionally use as a universe filter.

Phase 2 — autonomous mode (set autonomous: true in params):
  Enters PLACE_BUY for top-K coins not already in a position,
  generates CLOSE for coins that have fallen out of ranking.

Momentum score: M = log(close_t / close_{t-N})  (on 1h bars).
"""
import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

from execution.cost_filter import CostFilter
from strategies.bar_aggregator import BarAggregator
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


@dataclass
class _CoinState:
    agg_1h:       BarAggregator
    momentum:     Optional[float] = None
    rank_long:    Optional[int]   = None
    rank_short:   Optional[int]   = None
    has_position: bool            = False
    last_entry_ts: float          = 0.0


class RotationMomentumStrategy(BaseStrategy):
    """
    Rotation Momentum — cross-sectional momentum with rebalancing.

    Config params:
        momentum_lookback int   24    Lookback bars on 1h bars
        top_k             int   3     Number of long candidates
        bottom_k          int   3     Number of short candidates
        min_momentum      float 0.0   Min momentum score for long entry
        rebalance_minutes int   60    Rerank frequency
        autonomous        bool  false True = generate trades, False = scanner only
        stop_loss_pct     float 0.04  Stop loss
        take_profit_pct   float 0.015 Take profit
        max_hold_hours    float 8     Max hold
        min_cost_ratio    float 3.0   CostFilter ratio
    """

    def __init__(self, config: StrategyConfig, **kwargs):
        super().__init__(config, **kwargs)
        p = config.params

        self._lookback      = int(p.get("momentum_lookback", 24))
        self._top_k         = int(p.get("top_k",             3))
        self._bottom_k      = int(p.get("bottom_k",          3))
        self._min_momentum  = float(p.get("min_momentum",    0.0))
        self._rebal_min     = int(p.get("rebalance_minutes", 60))
        self._autonomous    = bool(p.get("autonomous",        False))
        self._sl_pct        = float(p.get("stop_loss_pct",   0.04))
        self._tp_pct        = float(p.get("take_profit_pct", 0.015))
        self._max_hold_s    = float(p.get("max_hold_hours",  8)) * 3600
        self._cost_filter   = CostFilter(min_ratio=float(p.get("min_cost_ratio", 3.0)))

        self._coins: dict[str, _CoinState] = {}
        for coin in config.coins:
            self._coins[coin] = _CoinState(
                agg_1h = BarAggregator(coin, 60, maxlen=200),
            )

        self._last_rebalance_ts: float  = 0.0
        self._ranking:           list   = []   # sorted list of (score, symbol)

    # ------------------------------------------------------------------
    # Public API (used by other strategies as filter)
    # ------------------------------------------------------------------

    def get_top_coins(self) -> list[str]:
        return [sym for _, sym in self._ranking[:self._top_k]]

    def get_bottom_coins(self) -> list[str]:
        return [sym for _, sym in self._ranking[-self._bottom_k:]]

    def get_rank(self, symbol: str) -> Optional[int]:
        for i, (_, sym) in enumerate(self._ranking):
            if sym == symbol:
                return i + 1
        return None

    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        state = self._coins.get(symbol)
        if state is None:
            return None

        bar_1h = state.agg_1h.update(bar)
        if bar_1h is None:
            return None

        # Rebalance if enough time has passed since last rebalance
        if ts - self._last_rebalance_ts >= self._rebal_min * 60:
            self._rebalance(ts)

        if not self._autonomous:
            return None

        # Autonomous mode: enter top-K, exit fallen coins
        return self._autonomous_signal(symbol, state, ts)

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if not self._autonomous:
            return None
        state = self._coins.get(symbol)
        if state is None or not state.has_position:
            return None
        # Exit if coin fell out of top-K
        rank = self.get_rank(symbol)
        if rank is None or rank > self._top_k:
            return StrategyDecision(
                action="CLOSE", symbol=symbol,
                reason=f"rank_exit rank={rank}",
            )
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        state = self._coins.get(symbol)
        if state:
            state.has_position = True
            state.last_entry_ts = ts
        return {
            "tp_price":         price * (1 + self._tp_pct),
            "stop_price":       price * (1 - self._sl_pct),
            "max_hold_seconds": int(self._max_hold_s),
        }

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        state = self._coins.get(symbol)
        if state:
            state.has_position = False
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        state = self._coins.get(symbol)
        if state is None:
            return {}
        rank = self.get_rank(symbol)
        n = len(self._ranking)
        return {
            "bars_1h":    len(state.agg_1h),
            "momentum":   round(state.momentum, 5) if state.momentum is not None else None,
            "rank":       rank,
            "n_ranked":   n,
            "is_top_k":   rank is not None and rank <= self._top_k,
            "is_bot_k":   rank is not None and rank > n - self._bottom_k,
            "has_position": state.has_position,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _rebalance(self, ts: float) -> None:
        scores = []
        for sym, state in self._coins.items():
            closes = state.agg_1h.closes()
            if len(closes) < self._lookback + 1:
                state.momentum = None
                continue
            m = math.log(closes[-1] / closes[-self._lookback - 1])
            state.momentum = m
            scores.append((m, sym))

        # Sort ascending: index 0 = weakest, index -1 = strongest
        scores.sort(key=lambda x: x[0])
        self._ranking = scores

        # Update per-coin rank info
        n = len(scores)
        for i, (score, sym) in enumerate(scores):
            if sym in self._coins:
                self._coins[sym].rank_long  = n - i       # 1=best
                self._coins[sym].rank_short = i + 1        # 1=weakest

        self._last_rebalance_ts = ts
        if scores:
            top = [s for _, s in reversed(scores[-self._top_k:])]
            log.debug("RotationMomentum rebalance: top=%s", top)

    def _autonomous_signal(self, symbol: str, state: _CoinState,
                           ts: float) -> Optional[StrategyDecision]:
        if state.has_position:
            return None

        rank = self.get_rank(symbol)
        n    = len(self._ranking)
        if rank is None or n == 0:
            return None

        # Long signal: top K + positive momentum
        is_long_candidate = (rank >= n - self._top_k + 1
                             and (state.momentum or 0) >= self._min_momentum)
        if is_long_candidate:
            expected_bps = self._tp_pct * 10000
            ok, cost_reason, _ = self._cost_filter.is_worth_taking(expected_bps)
            if not ok:
                return StrategyDecision(action="SKIP", symbol=symbol, reason=cost_reason)

            notional = min(
                self.config.capital_allocated_usd / max(self._top_k, 1),
                self.config.max_position_size_usd,
            )
            return StrategyDecision(
                action="PLACE_BUY",
                symbol=symbol,
                reason=f"rotation_top rank={rank}/{n} mom={state.momentum:.4f}",
                notional_usd=notional,
                stop_loss=None,
                take_profit=None,
                max_hold_seconds=int(self._max_hold_s),
            )

        return StrategyDecision(
            action="SKIP", symbol=symbol,
            reason=f"rank={rank}/{n} not_top{self._top_k}",
        )
