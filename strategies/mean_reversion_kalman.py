"""
mean_reversion_kalman.py — Mean reversion on Kalman fair value deviation.

Universe: BTC, ETH, SOL (most liquid, where intraday MR exists).
Enter LONG when z_t < -z_entry, SHORT when z_t > +z_entry.
Exit when |z_t| < z_exit.  Hard stop at |z_t| > z_stop.
"""
import logging
from collections import deque
from typing import Optional

import numpy as np

from econophysics.kalman_fair_value import KalmanFairValue
from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


class MeanReversionKalman(BaseStrategy):

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        p = config.params

        self._kalmans:    dict[str, KalmanFairValue] = {
            c: KalmanFairValue(
                process_noise=p.get("kalman_process_noise", 1e-6),
                obs_noise_mid=p.get("kalman_obs_noise", 1e-4),
            )
            for c in config.coins
        }
        self._innovations: dict[str, deque] = {c: deque(maxlen=300) for c in config.coins}
        self._z_cache:     dict[str, float] = {}
        self._fv_cache:    dict[str, float] = {}
        self._positions:   dict[str, dict]  = {}
        self._last_vol:    dict[str, deque] = {c: deque(maxlen=60) for c in config.coins}

    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        p   = self.config.params
        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            return None

        mid        = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000

        # Spread filter
        if spread_bps > p.get("spread_bps_max", 5.0):
            self._log_skip(symbol, "spread_too_wide", ts, mid, spread_bps)
            return None

        # Kalman update
        kalman = self._kalmans.get(symbol)
        if kalman is None:
            return None
        fv, drift = kalman.update(mid)
        self._fv_cache[symbol] = fv

        innovation = mid - fv
        inno_buf   = self._innovations[symbol]
        inno_buf.append(innovation)

        if len(inno_buf) < 30:
            return None

        sigma = float(np.std(list(inno_buf)))
        if sigma < 1e-12:
            return None

        z_t = innovation / sigma
        self._z_cache[symbol] = z_t

        # Volatility filter
        vol_buf = self._last_vol[symbol]
        if len(vol_buf) >= 10:
            realized_vol = float(np.std(list(vol_buf)))
            if realized_vol > p.get("vol_max_pct_per_min", 0.15) / 100.0:
                self._log_skip(symbol, "volatility_too_high", ts, mid, spread_bps)
                return None

        z_entry = p.get("z_entry", 2.0)
        z_exit  = p.get("z_exit",  0.0)
        z_stop  = p.get("z_stop",  3.5)

        # ── Exit check ──────────────────────────────────────────────
        if symbol in self._positions:
            pos  = self._positions[symbol]
            side = pos["side"]
            hold_s = ts - pos["opened_at"]

            if abs(z_t) > z_stop:
                return self._make_close(symbol, mid, ts, "stop_z")
            max_hold = int(p.get("max_hold_minutes", 30) * 60)
            if ts >= pos["max_hold_ts"]:
                return self._make_close(symbol, mid, ts, "max_hold")
            if side == "BUY"  and z_t >= -z_exit:
                return self._make_close(symbol, mid, ts, "z_reversion")
            if side == "SELL" and z_t <=  z_exit:
                return self._make_close(symbol, mid, ts, "z_reversion")
            return None

        # ── Entry check ─────────────────────────────────────────────
        max_pos = self.config.max_positions
        if len(self._positions) >= max_pos:
            self._log_skip(symbol, "max_positions_reached", ts, mid, spread_bps)
            return None

        if abs(z_t) < z_entry:
            return None

        side = "BUY" if z_t < 0 else "SELL"
        notional = self.config.max_position_size_usd
        size     = notional / max(mid, 1e-9)
        max_hold = int(p.get("max_hold_minutes", 30) * 60)

        if self.decision_logger:
            self.decision_logger.log_place(
                symbol, timestamp=ts, mid=mid, spread_bps=spread_bps,
                kalman_fv=fv, notional_usd=notional,
            )

        action = "PLACE_BUY" if side == "BUY" else "PLACE_SELL"
        entry  = ask if side == "BUY" else bid
        stop   = entry * (1 - 0.02) if side == "BUY" else entry * (1 + 0.02)
        tp     = fv

        return StrategyDecision(
            action=action, symbol=symbol, reason=f"z={z_t:.2f}",
            buy_price=entry if side == "BUY" else None,
            sell_price=entry if side == "SELL" else None,
            size=size, notional_usd=notional,
            stop_loss=stop, take_profit=tp, max_hold_seconds=max_hold,
            metadata={"z_t": z_t, "fv": fv},
        )

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        self._last_vol[symbol].append(bar.return_1m)
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p = self.config.params
        max_hold = int(p.get("max_hold_minutes", 30) * 60)
        fv = self._fv_cache.get(symbol, price)
        stop = price * (1 - 0.02) if side == "BUY" else price * (1 + 0.02)

        self._positions[symbol] = {
            "side":       side,
            "entry":      price,
            "size":       size,
            "tp":         fv,
            "stop":       stop,
            "opened_at":  ts,
            "max_hold_ts": ts + max_hold,
            "pos_id":     pos_id,
        }
        return {"tp_price": fv, "stop_price": stop, "max_hold_seconds": max_hold}

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self.on_orderbook_update(symbol, book, ts)

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        z  = self._z_cache.get(symbol, None)
        fv = self._fv_cache.get(symbol, None)
        return {"z_score": z, "kalman_fv": fv}

    # ------------------------------------------------------------------

    def _make_close(self, symbol: str, mid: float, ts: float,
                    reason: str) -> StrategyDecision:
        pos    = self._positions.get(symbol, {})
        hold_s = ts - pos.get("opened_at", ts)
        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "hold_s": hold_s, "pos_id": pos.get("pos_id")},
        )

    def _log_skip(self, symbol, reason, ts, mid, spread_bps):
        if self.decision_logger:
            self.decision_logger.log_skip(symbol, reason, timestamp=ts,
                                          mid=mid, spread_bps=spread_bps)
