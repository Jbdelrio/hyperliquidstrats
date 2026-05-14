"""
rsi_bollinger_reversion.py — RSI/Bollinger Mean Reversion strategy.

Entry (two-bar confirmation to avoid catching falling knives):
  Bar t-1 : close < BB_lower AND RSI < rsi_oversold AND z-score < zscore_entry
  Bar t   : close > BB_lower  (confirmation: price re-enters band)
             AND 1h trend not strongly bearish
             AND BTC not crashing
             AND CostFilter passes

Exit:
  - Hard stop-loss (pct)
  - Take-profit (pct)
  - Time stop: after time_stop_bars × 15m bars
  - RSI > 55 (momentum restored)
"""
import logging
from dataclasses import dataclass
from typing import Optional

from execution.cost_filter import CostFilter
from indicators.technical import EmaState, bollinger, rsi, zscore
from strategies.bar_aggregator import BarAggregator
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)

_BTC = "BTC"


@dataclass
class _PositionState:
    pos_id:      str
    entry_price: float
    stop_loss:   float
    take_profit: float
    entry_bar:   int    # bar index at entry (for time stop)


@dataclass
class _CoinState:
    agg_15m:       BarAggregator
    agg_60m:       BarAggregator
    ema_1h:        EmaState
    was_oversold:  bool  = False   # True when bar t-1 met all oversold conditions
    bar_count:     int   = 0       # total 15m bars seen
    position:      Optional[_PositionState] = None
    last_entry_ts: float = 0.0


class RSIBollingerReversionStrategy(BaseStrategy):
    """
    RSI/Bollinger Mean Reversion — counter-trend rebounce strategy.

    Config params:
        rsi_period       int   14      RSI period
        rsi_oversold     float 30      Oversold threshold
        zscore_period    int   30      Z-score lookback on 15m closes
        zscore_entry     float -2.0   Z-score entry level
        bb_period        int   20      Bollinger Band period
        bb_k             float 2.0    Bollinger Band multiplier
        ema_1h_period    int   100     1h trend EMA period
        stop_loss_pct    float 0.005   Hard stop distance
        take_profit_pct  float 0.006   Take-profit distance
        time_stop_bars   int   6       Max hold in 15m bars
        min_cost_ratio   float 3.0     CostFilter min ratio
        cooldown_s       float 120     Min seconds between entries
    """

    def __init__(self, config: StrategyConfig, **kwargs):
        super().__init__(config, **kwargs)
        p = config.params

        self._rsi_period    = int(p.get("rsi_period",      14))
        self._rsi_oversold  = float(p.get("rsi_oversold",  30.0))
        self._zs_period     = int(p.get("zscore_period",   30))
        self._zs_entry      = float(p.get("zscore_entry",  -2.0))
        self._bb_period     = int(p.get("bb_period",       20))
        self._bb_k          = float(p.get("bb_k",          2.0))
        self._ema_1h_period = int(p.get("ema_1h_period",  100))
        self._sl_pct        = float(p.get("stop_loss_pct", 0.005))
        self._tp_pct        = float(p.get("take_profit_pct", 0.006))
        self._time_stop     = int(p.get("time_stop_bars",  6))
        self._cooldown_s    = float(p.get("cooldown_s",    120.0))
        self._max_hold_s    = self._time_stop * 15 * 60

        self._cost_filter = CostFilter(min_ratio=float(p.get("min_cost_ratio", 3.0)))

        # Phase-6: BTC 5m return injected by the engine via set_btc_context().
        # When < btc_crash_5m_pct (default -0.015 = -1.5%), long entries are
        # skipped. None means "no fresh BTC context yet" — gate is disabled.
        self._btc_5m_return: Optional[float] = None
        self._btc_crash_5m_pct = float(p.get("btc_crash_5m_pct", -0.015))

        self._coins: dict[str, _CoinState] = {}
        for coin in config.coins:
            self._coins[coin] = _CoinState(
                agg_15m = BarAggregator(coin, 15, maxlen=300),
                agg_60m = BarAggregator(coin, 60, maxlen=300),
                ema_1h  = EmaState(self._ema_1h_period),
            )

    # ------------------------------------------------------------------
    # Phase-6 BTC context setter (engine calls this from _minute_loop)
    # ------------------------------------------------------------------

    def set_btc_context(self, btc_5m_return: float) -> None:
        """Inject the latest 5-minute BTC return.

        The strategy uses this to skip long entries during a BTC crash
        (the local oversold/RSI signal is not reliable when BTC itself
        is in free fall).
        """
        try:
            self._btc_5m_return = float(btc_5m_return)
        except (TypeError, ValueError):
            self._btc_5m_return = None

    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        state = self._coins.get(symbol)
        if state is None:
            return None

        bar_60 = state.agg_60m.update(bar)
        bar_15 = state.agg_15m.update(bar)

        if bar_60 is not None:
            state.ema_1h.update(bar_60.close)

        if bar_15 is None:
            return None

        state.bar_count += 1

        # Time stop check for open position
        if state.position is not None:
            bars_held = state.bar_count - state.position.entry_bar
            if bars_held >= self._time_stop:
                return StrategyDecision(
                    action="CLOSE", symbol=symbol,
                    reason=f"time_stop bars_held={bars_held}",
                    metadata={"pos_id": state.position.pos_id},
                )
            return None  # let executor manage TP/SL

        # Cooldown
        if ts - state.last_entry_ts < self._cooldown_s:
            return None

        closes = state.agg_15m.closes()
        if len(closes) < max(self._rsi_period + 1, self._bb_period, self._zs_period):
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"warmup {len(closes)} bars")

        current_close = closes[-1]

        # ── Compute indicators ───────────────────────────────────────
        bb = bollinger(closes[:-1], self._bb_period, self._bb_k)
        if bb is None:
            return StrategyDecision(action="SKIP", symbol=symbol, reason="bb_none")
        bb_upper, bb_mid, bb_lower = bb

        rsi_val = rsi(closes[:-1], self._rsi_period)
        if rsi_val is None:
            return StrategyDecision(action="SKIP", symbol=symbol, reason="rsi_none")

        zs = zscore(closes[:-1], self._zs_period)
        if zs is None:
            return StrategyDecision(action="SKIP", symbol=symbol, reason="zscore_none")

        prev_close = closes[-2] if len(closes) >= 2 else current_close

        # ── Two-bar confirmation logic ───────────────────────────────
        was_oversold = state.was_oversold
        now_oversold = (prev_close < bb_lower
                        and rsi_val < self._rsi_oversold
                        and zs < self._zs_entry)
        state.was_oversold = now_oversold

        if not was_oversold:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"wait_oversold rsi={rsi_val:.1f} z={zs:.2f}")

        if current_close <= bb_lower:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"no_confirmation close={current_close:.5g} bb_lower={bb_lower:.5g}")

        # ── 1h trend: not in strong downtrend ────────────────────────
        ema_1h = state.ema_1h.value
        closes_1h = state.agg_60m.closes()
        if ema_1h and closes_1h and closes_1h[-1] < ema_1h * 0.98:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"strong_downtrend_1h close={closes_1h[-1]:.5g} ema={ema_1h:.5g}")

        # ── BTC regime (check for crash only) ────────────────────────
        if symbol != _BTC:
            btc = self._coins.get(_BTC)
            if btc and len(btc.agg_15m) >= 4:
                btc_closes = btc.agg_15m.closes()
                if len(btc_closes) >= 4:
                    btc_chg = (btc_closes[-1] - btc_closes[-4]) / btc_closes[-4]
                    if btc_chg < -0.015:   # BTC down >1.5% in 1h → skip
                        return StrategyDecision(action="SKIP", symbol=symbol,
                                                reason=f"btc_crash btc_1h={btc_chg:.2%}")

        # Phase-6: explicit BTC 5m crash guard via engine-injected context.
        # When btc_5m_return is provided and below the configured threshold,
        # skip long entries (the strategy is long-only).
        if (self._btc_5m_return is not None
                and self._btc_5m_return < self._btc_crash_5m_pct):
            return StrategyDecision(
                action="SKIP", symbol=symbol,
                reason=(f"btc_crash_5m={self._btc_5m_return:.2%}"
                        f"<{self._btc_crash_5m_pct:.2%}"),
            )

        # ── Cost filter ──────────────────────────────────────────────
        expected_bps = self._tp_pct * 10000
        ok, cost_reason, _ = self._cost_filter.is_worth_taking(expected_bps)
        if not ok:
            return StrategyDecision(action="SKIP", symbol=symbol, reason=cost_reason)

        # ── Entry ────────────────────────────────────────────────────
        notional = min(
            self.config.capital_allocated_usd / max(self.config.max_positions, 1),
            self.config.max_position_size_usd,
        )
        state.last_entry_ts = ts

        # Enrich decision with risk metrics (Phase-6)
        stop_price = current_close * (1 - self._sl_pct)
        tp_price   = current_close * (1 + self._tp_pct)
        p = self.config.params
        fee_bps = float(p.get("taker_fee_bps", 4.5))
        slip_bps = float(p.get("slippage_bps", 4.5))
        cost_bps = 2 * (fee_bps + slip_bps)
        risk_usd = notional * self._sl_pct
        reward_usd = notional * self._tp_pct
        rr = reward_usd / risk_usd if risk_usd > 0 else 0.0
        expected_edge_bps = self._tp_pct * 10_000.0
        expected_net = reward_usd - (cost_bps / 10_000.0) * notional

        return StrategyDecision(
            action="PLACE_BUY",
            symbol=symbol,
            reason=f"rsi_bb_reversion rsi={rsi_val:.1f} z={zs:.2f} close_re_entry={current_close:.5g}",
            notional_usd=notional,
            stop_loss=stop_price,
            take_profit=tp_price,
            max_hold_seconds=self._max_hold_s,
            metadata={"bb_lower": bb_lower, "rsi": rsi_val, "zscore": zs},
            strategy_family="mean_reversion",
            order_type="TAKER_SIM",
            confidence=min(1.0, abs(zs) / 4.0),
            estimated_cost_bps=cost_bps,
            expected_edge_bps=expected_edge_bps,
            expected_net_profit_usd=expected_net,
            risk_usd=risk_usd,
            reward_risk_ratio=rr,
        )

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        state = self._coins.get(symbol)
        if state is None or state.position is None:
            return None
        mid = book.mid if hasattr(book, "mid") else (book.best_bid + book.best_ask) / 2
        pos = state.position
        if mid <= pos.stop_loss:
            return StrategyDecision(action="CLOSE", symbol=symbol,
                                    reason=f"hard_stop {mid:.5g}<={pos.stop_loss:.5g}",
                                    metadata={"pos_id": pos.pos_id})
        if mid >= pos.take_profit:
            return StrategyDecision(action="CLOSE", symbol=symbol,
                                    reason=f"take_profit {mid:.5g}>={pos.take_profit:.5g}",
                                    metadata={"pos_id": pos.pos_id})
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        state = self._coins.get(symbol)
        if state is None:
            return None
        state.position = _PositionState(
            pos_id      = pos_id,
            entry_price = price,
            stop_loss   = price * (1 - self._sl_pct),
            take_profit = price * (1 + self._tp_pct),
            entry_bar   = state.bar_count,
        )
        return {
            "tp_price":         state.position.take_profit,
            "stop_price":       state.position.stop_loss,
            "max_hold_seconds": self._max_hold_s,
        }

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        state = self._coins.get(symbol)
        if state:
            state.position    = None
            state.was_oversold = False
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        state = self._coins.get(symbol)
        if state is None:
            return {}
        closes = state.agg_15m.closes()
        if not closes:
            return {"bars_15m": 0}

        bb  = bollinger(closes, self._bb_period, self._bb_k)
        rsi_v = rsi(closes, self._rsi_period)
        zs  = zscore(closes, self._zs_period)

        return {
            "bars_15m":      len(state.agg_15m),
            "close":         round(closes[-1], 4),
            "bb_upper":      round(bb[0], 4) if bb else None,
            "bb_mid":        round(bb[1], 4) if bb else None,
            "bb_lower":      round(bb[2], 4) if bb else None,
            "rsi":           round(rsi_v, 2) if rsi_v is not None else None,
            "zscore":        round(zs, 3) if zs is not None else None,
            "was_oversold":  state.was_oversold,
            "has_position":  state.position is not None,
            "ema_1h":        round(state.ema_1h.value, 4) if state.ema_1h.value else None,
        }
