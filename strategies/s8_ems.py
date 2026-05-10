"""
s8_ems.py — S8 Econophysics Maker Scalping.

Orchestrates 5 sensors:
  1. HurstLocalEstimator       → size multiplier (regime)
  2. HARRealizedVolatility     → size multiplier (vol prediction)
  3. WaveletSingularityDetector → cancel quotes on singularity
  4. BouchaudImpactModel       → quote skew (order-flow pressure)
  5. KalmanFairValue           → clean fair value + drift skew

Inherits BaseStrategy. Disabled by default (needs spread > 4 bps to operate).
"""
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from econophysics.hurst_local import HurstLocalEstimator
from econophysics.har_rv import HARRealizedVolatility
from econophysics.wavelet_singularity import WaveletSingularityDetector
from econophysics.bouchaud_impact import BouchaudImpactModel
from econophysics.kalman_fair_value import KalmanFairValue

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-coin state
# ---------------------------------------------------------------------------

@dataclass
class CoinState:
    symbol:   str
    hurst:    HurstLocalEstimator        = field(default_factory=lambda: HurstLocalEstimator(window=300))
    har_rv:   HARRealizedVolatility      = field(default_factory=HARRealizedVolatility)
    wavelet:  WaveletSingularityDetector = field(default_factory=WaveletSingularityDetector)
    bouchaud: BouchaudImpactModel        = field(default_factory=BouchaudImpactModel)
    kalman:   KalmanFairValue            = field(default_factory=lambda: KalmanFairValue())

    open_position: Optional[dict] = None
    last_quote_ts: float = 0.0
    fills:         int   = 0
    pnl:           float = 0.0


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------

class S8EconophysicsMakerScalping(BaseStrategy):

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        self.coin_states: dict[str, CoinState] = {
            s: CoinState(symbol=s) for s in config.coins
        }
        self._pending_ids: dict[str, list[str]] = {s: [] for s in config.coins}
        self.daily_pnl: float = 0.0

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        state = self.coin_states.get(symbol)
        if state is None:
            return None

        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            if self.decision_logger:
                self.decision_logger.log_skip(
                    symbol, "insufficient_orderbook", timestamp=ts,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                )
            return None

        mid        = (bid + ask) / 2
        spread     = ask - bid
        spread_bps = spread / mid * 10_000

        fv, drift = state.kalman.update(mid)
        state.hurst.update(mid)

        p       = self.config.params
        min_bps = p.get("min_spread_bps", 4.0)
        max_bps = p.get("max_spread_bps", 20.0)

        if not (min_bps <= spread_bps <= max_bps):
            reason = "spread_too_tight" if spread_bps < min_bps else "spread_too_wide"
            if self.decision_logger:
                self.decision_logger.log_skip(
                    symbol, reason, timestamp=ts,
                    mid=mid, spread_bps=spread_bps,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                    kalman_fv=fv,
                )
            return self._cancel_decision(symbol)

        if state.wavelet.is_alert_active(ts):
            if self.decision_logger:
                self.decision_logger.log_skip(
                    symbol, "wavelet_alert_active", timestamp=ts,
                    mid=mid, spread_bps=spread_bps,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                    kalman_fv=fv,
                )
            return self._cancel_decision(symbol)

        hurst_mult = state.hurst.get_size_multiplier()
        if hurst_mult <= 0.0:
            if self.decision_logger:
                self.decision_logger.log_skip(
                    symbol, "hurst_unfavorable", timestamp=ts,
                    mid=mid, spread_bps=spread_bps,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                    kalman_fv=fv,
                )
            return self._cancel_decision(symbol)

        har_mult = state.har_rv.get_size_multiplier()
        if har_mult < 0.3:
            if self.decision_logger:
                self.decision_logger.log_skip(
                    symbol, "har_rv_too_high", timestamp=ts,
                    mid=mid, spread_bps=spread_bps,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                    kalman_fv=fv,
                )
            return self._cancel_decision(symbol)

        if state.open_position is not None:
            if self.decision_logger:
                self.decision_logger.log_skip(
                    symbol, "max_positions_reached", timestamp=ts,
                    mid=mid, spread_bps=spread_bps,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                    kalman_fv=fv,
                )
            return None

        refresh_s = p.get("quote_refresh_s", 5.0)
        if (ts - state.last_quote_ts) < refresh_s:
            return None

        return self._compute_quotes(state, bid, ask, mid, spread, fv, drift,
                                    hurst_mult, har_mult, ts)

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        state = self.coin_states.get(symbol)
        if state is None:
            return
        state.bouchaud.add_trade(
            ts, trade.price, trade.volume_usd,
            trade.best_bid, trade.best_ask, trade.side,
        )
        state.wavelet.update(trade.price, ts)

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        state = self.coin_states.get(symbol)
        if state:
            state.har_rv.update(bar.return_1m)
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        state = self.coin_states.get(symbol)
        if state is None:
            return None

        state.fills += 1
        notional_usd = size * price

        p          = self.config.params
        stop_bps   = p.get("stop_loss_bps", 30) / 10_000
        max_hold_s = int(p.get("max_hold_s", 60))
        tp_capture = 0.60

        stop_dist = price * stop_bps
        if side == "BUY":
            tp_price   = price + stop_dist * tp_capture
            stop_price = price * (1 - stop_bps)
        else:
            tp_price   = price - stop_dist * tp_capture
            stop_price = price * (1 + stop_bps)

        state.open_position = {
            "side":        side,
            "entry":       price,
            "size":        size,
            "notional":    notional_usd,
            "tp":          tp_price,
            "stop":        stop_price,
            "opened_at":   ts,
            "max_hold_ts": ts + max_hold_s,
            "pos_id":      pos_id,
        }
        return {
            "tp_price":         round(tp_price, 8),
            "stop_price":       round(stop_price, 8),
            "max_hold_seconds": max_hold_s,
        }

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        state = self.coin_states.get(symbol)
        if state is None or state.open_position is None:
            return None

        bid = getattr(book, "best_bid", None) or getattr(book, "mid", None)
        ask = getattr(book, "best_ask", None) or getattr(book, "mid", None)
        mid = getattr(book, "mid", None)
        if mid is None:
            mid = (bid + ask) / 2 if bid and ask else None
        if mid is None:
            return None

        pos  = state.open_position
        side = pos["side"]

        stop_hit = (side == "BUY"  and mid <= pos["stop"]) or \
                   (side == "SELL" and mid >= pos["stop"])
        tp_hit   = (side == "BUY"  and (bid or mid) >= pos["tp"]) or \
                   (side == "SELL" and (ask or mid) <= pos["tp"])
        max_hold = ts >= pos["max_hold_ts"]

        if not (stop_hit or tp_hit or max_hold):
            return None

        if stop_hit:
            reason     = "stop_loss"
            exit_price = bid if side == "BUY" else ask
        elif tp_hit:
            reason     = "take_profit"
            exit_price = pos["tp"]
        else:
            reason     = "max_hold"
            exit_price = mid

        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={
                "exit_price": exit_price or mid,
                "hold_s":     ts - pos["opened_at"],
                "pos_id":     pos.get("pos_id"),
            },
        )

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        state = self.coin_states.get(symbol)
        if state:
            state.open_position = None
            state.pnl    += pnl_net
            self.daily_pnl += pnl_net
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        state = self.coin_states.get(symbol)
        if state is None:
            return {}
        return {
            "hurst":           state.hurst.h,
            "hurst_regime":    state.hurst.get_regime(),
            "har_rv_forecast": state.har_rv.predict_vol(),
            "har_ratio":       state.har_rv.get_vol_ratio(),
            "wavelet_alert":   state.wavelet.is_alert_active(time.time()),
            "has_position":    state.open_position is not None,
        }

    # ------------------------------------------------------------------
    # Engine-callable helpers (S8-specific, duck-typed by engine)
    # ------------------------------------------------------------------

    def register_pending(self, symbol: str, order_ids: list[str]) -> None:
        self._pending_ids[symbol] = order_ids

    def clear_pending(self, symbol: str) -> None:
        self._pending_ids[symbol] = []

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _compute_quotes(self, state: CoinState,
                        bid: float, ask: float, mid: float, spread: float,
                        fv: float, drift: float,
                        hurst_mult: float, har_mult: float,
                        ts: float) -> Optional[StrategyDecision]:
        p             = self.config.params
        bouchaud_skew = state.bouchaud.get_quote_skew(ts, spread)
        drift_skew    = drift * 5
        total_skew    = bouchaud_skew + drift_skew

        base_notional = self.config.capital_allocated_usd * p.get("base_notional_pct", 0.04)
        size_mult     = hurst_mult * har_mult
        notional_usd  = self.compute_order_notional(
            base_notional * size_mult * p.get("max_leverage", 5)
        )

        if notional_usd < 10:
            if self.decision_logger:
                self.decision_logger.log_skip(
                    state.symbol, "notional_too_small", timestamp=ts,
                    mid=mid, spread_bps=spread / mid * 10_000,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                    kalman_fv=fv,
                )
            return None

        size_units = notional_usd / max(fv, 1e-9)
        buy_price  = fv - spread / 2 + total_skew
        sell_price = fv + spread / 2 + total_skew

        if buy_price >= ask:
            buy_price = ask - ask * 5e-5
        if sell_price <= bid:
            sell_price = bid + bid * 5e-5
        if buy_price >= sell_price:
            if self.decision_logger:
                self.decision_logger.log_skip(
                    state.symbol, "spread_invalid_after_skew", timestamp=ts,
                    mid=mid, spread_bps=spread / mid * 10_000,
                    hurst=state.hurst.h,
                    har_rv_forecast=state.har_rv.predict_vol(),
                    kalman_fv=fv,
                )
            return None

        state.last_quote_ts = ts

        if self.decision_logger:
            self.decision_logger.log_place(
                state.symbol, timestamp=ts,
                mid=mid, spread_bps=spread / mid * 10_000,
                hurst=state.hurst.h,
                har_rv_forecast=state.har_rv.predict_vol(),
                kalman_fv=fv,
                buy_price=round(buy_price, 8),
                sell_price=round(sell_price, 8),
                size=size_units,
                notional_usd=notional_usd,
            )

        return StrategyDecision(
            action="PLACE_QUOTES",
            symbol=state.symbol,
            buy_price=round(buy_price, 8),
            sell_price=round(sell_price, 8),
            size=size_units,
            notional_usd=notional_usd,
            metadata={
                "hurst":         state.hurst.h,
                "hurst_regime":  state.hurst.get_regime(),
                "har_ratio":     state.har_rv.get_vol_ratio(),
                "bouchaud_skew": bouchaud_skew,
                "drift_skew":    drift_skew,
                "kalman_fv":     fv,
                "spread_bps":    spread / mid * 10_000,
                "size_mult":     size_mult,
            },
        )

    def _cancel_decision(self, symbol: str) -> Optional[StrategyDecision]:
        ids = self._pending_ids.get(symbol, [])
        if not ids:
            return None
        return StrategyDecision(
            action="CANCEL_QUOTES",
            symbol=symbol,
            metadata={"order_ids": list(ids)},
        )
