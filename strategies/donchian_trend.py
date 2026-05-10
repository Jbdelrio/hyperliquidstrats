"""
donchian_trend.py — Donchian Trend Breakout strategy.

Signal logic (all conditions required):
  1. 15m close > Donchian upper (N bars, excluding current)
  2. 1h  close > EMA(ema_1h_period) on 1h bars
  3. BTC 4h close > EMA(btc_regime_ema) on 4h bars  [skipped during warmup]
  4. 15m volume  > vol_multiplier × SMA(volume, vol_period) on 15m
  5. CostFilter: expected_move_bps >= min_ratio × round_trip_cost

Trailing stop: updated each new 15m bar → max(current_ts, Donchian_mid).
Hard stop / take-profit delegated to executor via on_fill() return value.
"""
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from execution.cost_filter import CostFilter
from indicators.technical import EmaState, atr, donchian, volume_sma
from strategies.bar_aggregator import BarAggregator
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)

_BTC = "BTC"


@dataclass
class _PositionState:
    pos_id:        str
    entry_price:   float
    trailing_stop: float
    stop_loss:     float
    take_profit:   float
    side:          str = "BUY"


@dataclass
class _CoinState:
    agg_15m:  BarAggregator
    agg_60m:  BarAggregator
    agg_240m: BarAggregator
    ema_1h:   EmaState
    ema_4h:   EmaState         # only meaningful for BTC
    position: Optional[_PositionState] = None
    last_entry_ts: float = 0.0


class DonchianTrendStrategy(BaseStrategy):
    """
    Donchian Trend Breakout — primary trend-following strategy.

    Config params (all in config_v9.json under "params"):
        donchian_n         int   48      Donchian channel lookback on 15m bars
        ema_1h_period      int   50      EMA period for 1h trend filter
        btc_regime_ema     int   200     EMA period for BTC 4h regime filter
        vol_period         int   20      Volume SMA period on 15m
        vol_multiplier     float 1.2     Min volume multiplier vs SMA
        stop_loss_pct      float 0.006   Hard stop-loss distance
        take_profit_pct    float 0.008   Take-profit distance
        trailing_mode      str   "mid"   "mid" = Donchian mid as trailing stop
        min_cost_ratio     float 3.0     CostFilter min reward/risk ratio
        max_hold_hours     float 12      Max hold time
        cooldown_s         float 60      Minimum seconds between entries (per coin)
    """

    def __init__(self, config: StrategyConfig, **kwargs):
        super().__init__(config, **kwargs)
        p = config.params

        self._donchian_n       = int(p.get("donchian_n",       48))
        self._ema_1h_period    = int(p.get("ema_1h_period",    50))
        self._btc_regime_ema   = int(p.get("btc_regime_ema",  200))
        self._vol_period       = int(p.get("vol_period",       20))
        self._vol_mult         = float(p.get("vol_multiplier",  1.2))
        self._sl_pct           = float(p.get("stop_loss_pct",   0.006))
        self._tp_pct           = float(p.get("take_profit_pct", 0.008))
        self._max_hold_s       = float(p.get("max_hold_hours",  12)) * 3600
        self._cooldown_s       = float(p.get("cooldown_s",      60))

        self._cost_filter = CostFilter(
            min_ratio=float(p.get("min_cost_ratio", 3.0))
        )

        self._coins: dict[str, _CoinState] = {}
        for coin in config.coins:
            self._coins[coin] = _CoinState(
                agg_15m  = BarAggregator(coin, 15,  maxlen=300),
                agg_60m  = BarAggregator(coin, 60,  maxlen=300),
                agg_240m = BarAggregator(coin, 240, maxlen=300),
                ema_1h   = EmaState(self._ema_1h_period),
                ema_4h   = EmaState(self._btc_regime_ema),
            )

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        state = self._coins.get(symbol)
        if state is None:
            return None

        # Feed all timeframe aggregators
        bar_240 = state.agg_240m.update(bar)
        bar_60  = state.agg_60m.update(bar)
        bar_15  = state.agg_15m.update(bar)

        # Update 4h EMA (BTC regime filter)
        if bar_240 is not None:
            state.ema_4h.update(bar_240.close)

        # Update 1h EMA (trend filter)
        if bar_60 is not None:
            state.ema_1h.update(bar_60.close)

        # Main signal check on every new 15m candle
        if bar_15 is None:
            return None

        # Update trailing stop for any open position
        self._update_trailing_stop(state)

        # Skip if already in a position for this coin
        if state.position is not None:
            return None

        # Cooldown between consecutive entries
        if ts - state.last_entry_ts < self._cooldown_s:
            return None

        return self._check_entry(symbol, state, ts)

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        state = self._coins.get(symbol)
        if state is None or state.position is None:
            return None
        pos = state.position
        mid = book.mid if hasattr(book, "mid") else (book.best_bid + book.best_ask) / 2

        reason = None
        if mid <= pos.trailing_stop:
            reason = f"trailing_stop={pos.trailing_stop:.5g}"
        elif mid <= pos.stop_loss:
            reason = f"hard_stop={pos.stop_loss:.5g}"
        elif mid >= pos.take_profit:
            reason = f"take_profit={pos.take_profit:.5g}"

        if reason:
            return StrategyDecision(
                action="CLOSE", symbol=symbol,
                reason=reason,
                metadata={"pos_id": pos.pos_id},
            )
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        state = self._coins.get(symbol)
        if state is None:
            return None

        # Calculate trailing stop seed = current Donchian mid
        dc = donchian(state.agg_15m.highs(), state.agg_15m.lows(), self._donchian_n)
        trailing_stop_seed = dc[1] if dc else price * (1 - self._sl_pct)

        state.position = _PositionState(
            pos_id        = pos_id,
            entry_price   = price,
            trailing_stop = min(trailing_stop_seed, price * (1 - self._sl_pct)),
            stop_loss     = price * (1 - self._sl_pct),
            take_profit   = price * (1 + self._tp_pct),
            side          = side,
        )
        state.last_entry_ts = ts
        return {
            "tp_price":         state.position.take_profit,
            "stop_price":       state.position.stop_loss,
            "max_hold_seconds": int(self._max_hold_s),
        }

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        state = self._coins.get(symbol)
        if state:
            state.position = None
        super().on_position_closed(symbol, pnl_net, exit_reason)

    # ------------------------------------------------------------------
    # Calibration data for GUI
    # ------------------------------------------------------------------

    def get_calibration_data(self, symbol: str) -> dict:
        state = self._coins.get(symbol)
        if state is None:
            return {}
        closes_15  = state.agg_15m.closes()
        highs_15   = state.agg_15m.highs()
        lows_15    = state.agg_15m.lows()
        volumes_15 = state.agg_15m.volumes()

        dc = donchian(highs_15, lows_15, self._donchian_n) if len(highs_15) >= self._donchian_n else None
        vol_sma = volume_sma(volumes_15, self._vol_period) if len(volumes_15) >= self._vol_period else None
        ema_1h  = state.ema_1h.value
        ema_4h  = state.ema_4h.value if symbol == _BTC else None
        has_pos = state.position is not None
        atv     = atr(highs_15, lows_15, closes_15, 14) if len(highs_15) > 14 else None

        return {
            "bars_15m":      len(state.agg_15m),
            "bars_1h":       len(state.agg_60m),
            "donchian_upper": round(dc[0], 4) if dc else None,
            "donchian_mid":   round(dc[1], 4) if dc else None,
            "donchian_lower": round(dc[2], 4) if dc else None,
            "ema_1h":         round(ema_1h, 4) if ema_1h else None,
            "btc_ema_4h":     round(ema_4h, 4) if ema_4h else None,
            "vol_sma_15m":   round(vol_sma, 2) if vol_sma else None,
            "atr_15m":       round(atv, 6) if atv else None,
            "has_position":  has_pos,
            "trailing_stop": round(state.position.trailing_stop, 4) if has_pos else None,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _update_trailing_stop(self, state: _CoinState) -> None:
        if state.position is None:
            return
        dc = donchian(state.agg_15m.highs(), state.agg_15m.lows(), self._donchian_n)
        if dc:
            new_ts = max(state.position.trailing_stop, dc[1])
            state.position.trailing_stop = new_ts

    def _check_entry(self, symbol: str, state: _CoinState,
                     ts: float) -> Optional[StrategyDecision]:
        closes_15  = state.agg_15m.closes()
        highs_15   = state.agg_15m.highs()
        lows_15    = state.agg_15m.lows()
        volumes_15 = state.agg_15m.volumes()

        if len(closes_15) < self._donchian_n + 1:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"warmup donchian {len(closes_15)}/{self._donchian_n}")

        # ── 1. Donchian breakout ─────────────────────────────────────
        dc = donchian(highs_15[:-1], lows_15[:-1], self._donchian_n)
        if dc is None:
            return StrategyDecision(action="SKIP", symbol=symbol, reason="donchian_none")
        upper, mid, lower = dc
        current_close = closes_15[-1]
        if current_close <= upper:
            return StrategyDecision(action="SKIP", symbol=symbol,
                                    reason=f"no_breakout close={current_close:.5g} upper={upper:.5g}")

        # ── 2. 1h EMA trend filter ───────────────────────────────────
        closes_1h = state.agg_60m.closes()
        ema_1h    = state.ema_1h.value
        if ema_1h and closes_1h:
            if closes_1h[-1] <= ema_1h:
                return StrategyDecision(action="SKIP", symbol=symbol,
                                        reason=f"1h_downtrend close={closes_1h[-1]:.5g} ema={ema_1h:.5g}")

        # ── 3. BTC 4h regime filter (permissive during warmup) ───────
        if symbol != _BTC:
            btc_state = self._coins.get(_BTC)
            if btc_state and btc_state.ema_4h.ready(50):
                btc_4h_closes = btc_state.agg_240m.closes()
                if btc_4h_closes and btc_state.ema_4h.value:
                    if btc_4h_closes[-1] < btc_state.ema_4h.value:
                        return StrategyDecision(
                            action="SKIP", symbol=symbol,
                            reason="btc_regime_bearish",
                        )

        # ── 4. Volume filter ─────────────────────────────────────────
        if len(volumes_15) >= self._vol_period:
            vsma = volume_sma(volumes_15[:-1], self._vol_period)
            if vsma and volumes_15[-1] < self._vol_mult * vsma:
                return StrategyDecision(action="SKIP", symbol=symbol,
                                        reason=f"low_volume v={volumes_15[-1]:.0f} sma={vsma:.0f}")

        # ── 5. Cost filter ───────────────────────────────────────────
        dc_range_bps = (upper - lower) / lower * 10000
        ok, cost_reason, _ = self._cost_filter.is_worth_taking(dc_range_bps)
        if not ok:
            return StrategyDecision(action="SKIP", symbol=symbol, reason=cost_reason)

        # ── All conditions met → place buy ───────────────────────────
        notional = min(
            self.config.capital_allocated_usd / max(self.config.max_positions, 1),
            self.config.max_position_size_usd,
        )
        return StrategyDecision(
            action="PLACE_BUY",
            symbol=symbol,
            reason=f"donchian_breakout close={current_close:.5g} upper={upper:.5g}",
            notional_usd=notional,
            stop_loss=current_close * (1 - self._sl_pct),
            take_profit=current_close * (1 + self._tp_pct),
            max_hold_seconds=int(self._max_hold_s),
            metadata={
                "donchian_upper": upper,
                "donchian_mid": mid,
                "ema_1h": ema_1h,
            },
        )
