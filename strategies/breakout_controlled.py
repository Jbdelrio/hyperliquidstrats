"""
breakout_controlled.py — Resistance breakout with volume confirmation.

Enters LONG when price breaks above N-bar resistance with VR > threshold.
Stop placed below resistance; TP at +5%; invalidation if price drops back.
"""
import logging
from collections import deque
from typing import Optional

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


class BreakoutControlled(BaseStrategy):

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        n = config.params.get("lookback_bars", 60)

        self._bars:    dict[str, deque] = {c: deque(maxlen=n + 5) for c in config.coins}
        self._vol24h:  dict[str, deque] = {c: deque(maxlen=1440) for c in config.coins}

        # Active signals: {symbol: {"resistance": float, "vr": float, "ts": float}}
        self._signals:         dict[str, dict] = {}
        self._positions:       dict[str, dict] = {}
        # Bridge: stores resistance/vr between PLACE_BUY decision and on_fill
        self._pending_entries: dict[str, dict] = {}

    # ------------------------------------------------------------------
    # BaseStrategy interface
    # ------------------------------------------------------------------

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        p   = self.config.params
        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            return None

        mid        = (bid + ask) / 2
        spread_bps = (ask - bid) / mid * 10_000

        # ── Exit check ──────────────────────────────────────────────
        if symbol in self._positions:
            d = self._check_exit(symbol, mid, ts)
            if d:
                return d

        # ── Entry check ─────────────────────────────────────────────
        if symbol in self._positions:
            return None

        max_pos = self.config.max_positions
        if len(self._positions) >= max_pos:
            self._log_skip(symbol, "max_positions_reached", ts, mid, spread_bps)
            return None

        signal = self._signals.get(symbol)
        if signal is None:
            return None

        if spread_bps > p.get("spread_bps_max", 15.0):
            self._log_skip(symbol, "spread_too_wide", ts, mid, spread_bps)
            return None

        resistance = signal["resistance"]
        if mid < resistance * 0.99:
            # Price dropped back below resistance — signal invalidated
            self._signals.pop(symbol, None)
            self._log_skip(symbol, "breakout_invalidated", ts, mid, spread_bps)
            return None

        # Signal still valid — entry
        notional = min(
            self.config.max_position_size_usd,
            self.config.capital_allocated_usd,
        )
        tp_pct   = p.get("take_profit_pct", 5.0) / 100.0
        stop_pct = p.get("stop_below_resistance_pct", 0.5) / 100.0
        tp_price   = mid * (1 + tp_pct)
        stop_price = resistance * (1 - stop_pct)
        max_hold   = int(p.get("max_hold_hours", 2) * 3600)
        size       = notional / max(ask, 1e-9)

        if self.decision_logger:
            self.decision_logger.log_place(
                symbol, timestamp=ts, mid=mid, spread_bps=spread_bps,
                notional_usd=notional,
            )

        # Consume signal; save resistance so on_fill can read it after the pop
        self._signals.pop(symbol, None)
        self._pending_entries[symbol] = {"resistance": resistance, "vr": signal["vr"]}

        # ── Enrich decision with risk metrics (Phase-6) ──────────
        fee_bps = float(p.get("taker_fee_bps", 4.5))
        slip_bps = float(p.get("slippage_bps", 4.5))
        cost_bps = 2 * (fee_bps + slip_bps)  # round-trip
        risk_usd = max(notional * abs(mid - stop_price) / mid, 1e-9)
        reward_usd = notional * (tp_price - mid) / mid
        rr = reward_usd / risk_usd if risk_usd > 0 else 0.0
        expected_edge_bps = (tp_price - mid) / mid * 10_000.0
        expected_net = reward_usd - (cost_bps / 10_000.0) * notional

        return StrategyDecision(
            action="PLACE_BUY", symbol=symbol,
            buy_price=ask, size=size, notional_usd=notional,
            stop_loss=stop_price, take_profit=tp_price,
            max_hold_seconds=max_hold,
            metadata={"resistance": resistance},
            strategy_family="breakout",
            order_type="TAKER_SIM",
            confidence=min(1.0, signal["vr"] / 3.0),
            estimated_cost_bps=cost_bps,
            expected_edge_bps=expected_edge_bps,
            expected_net_profit_usd=expected_net,
            risk_usd=risk_usd,
            reward_risk_ratio=rr,
        )

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        bars   = self._bars.get(symbol)
        vol24h = self._vol24h.get(symbol)
        if bars is None:
            return None

        bars.append((bar.high, bar.low, bar.close, bar.volume_usd))
        vol24h.append(bar.volume_usd)

        self._detect_breakout(symbol, bar, ts)
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p = self.config.params
        pending = self._pending_entries.pop(symbol, None)
        resistance = (pending or {}).get("resistance", price * 0.995)
        tp_pct   = p.get("take_profit_pct", 5.0) / 100.0
        stop_pct = p.get("stop_below_resistance_pct", 0.5) / 100.0
        tp    = price * (1 + tp_pct)
        stop  = resistance * (1 - stop_pct)
        max_hold = int(p.get("max_hold_hours", 2) * 3600)

        self._positions[symbol] = {
            "side":         side,
            "entry":        price,
            "size":         size,
            "notional":     size * price,
            "tp":           tp,
            "stop":         stop,
            "resistance":   resistance,
            "opened_at":    ts,
            "max_hold_ts":  ts + max_hold,
            "pos_id":       pos_id,
        }
        return {"tp_price": tp, "stop_price": stop, "max_hold_seconds": max_hold}

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            return None
        return self._check_exit(symbol, mid, ts)

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        bars  = list(self._bars.get(symbol, []))
        n     = self.config.params.get("lookback_bars", 60)
        resistance = None
        current_close = None
        bo_pct = None
        vr = None

        if len(bars) >= n:
            highs = [b[0] for b in bars[-n:]]
            resistance = max(highs)
            current_close = bars[-1][2]
            if resistance > 0:
                bo_pct = (current_close - resistance) / resistance * 100

        vol24h = list(self._vol24h.get(symbol, []))
        if bars and vol24h:
            vol_15m = sum(b[3] for b in bars[-15:]) if len(bars) >= 15 else None
            avg_24h = sum(vol24h) / len(vol24h) if vol24h else None
            if vol_15m and avg_24h and avg_24h > 0:
                vr = vol_15m / avg_24h

        return {
            "resistance":    resistance,
            "current_close": current_close,
            "bo_pct":        bo_pct,
            "volume_ratio":  vr,
            "signal_active": symbol in self._signals,
            "pending_entry": symbol in self._pending_entries,
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _detect_breakout(self, symbol: str, bar: BarData, ts: float) -> None:
        p    = self.config.params
        bars = list(self._bars.get(symbol, []))
        n    = p.get("lookback_bars", 60)
        if len(bars) < n:
            return

        highs = [b[0] for b in bars[-n:-1]]  # exclude current bar
        if not highs:
            return
        resistance = max(highs)

        bo_max = p.get("bo_max_pct", 4.0) / 100.0
        if bar.close <= resistance:
            return
        bo = (bar.close - resistance) / resistance
        if bo > bo_max:
            return  # already pumped too far

        # Volume ratio
        vol24h = list(self._vol24h.get(symbol, []))
        vol_15m = sum(b[3] for b in bars[-15:]) if len(bars) >= 15 else 0.0
        avg_24h = sum(vol24h) / len(vol24h) if vol24h else 0.0
        if avg_24h <= 0:
            return
        vr = vol_15m / avg_24h
        if vr < p.get("vr_min", 1.5):
            return

        # Phase-6 close-strength filter: require the close to be in the
        # upper portion of the bar's range. A weak close (e.g. wick that
        # broke resistance but the body closed low) is a poor breakout.
        cs_min = p.get("close_strength_min", 0.0)
        if cs_min > 0.0:
            high = bar.high
            low  = bar.low
            rng  = max(high - low, 1e-9)
            close_strength = (bar.close - low) / rng
            if close_strength < cs_min:
                return

        # Signal confirmed
        self._signals[symbol] = {"resistance": resistance, "vr": vr, "ts": ts}
        log.debug("BreakoutControlled signal: %s resistance=%.4f vr=%.2f", symbol, resistance, vr)

    def _check_exit(self, symbol: str, mid: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        tp_hit   = mid >= pos["tp"]
        stop_hit = mid <= pos["stop"]
        max_hold = ts >= pos["max_hold_ts"]
        # Invalidation: price fell back below resistance
        invalid  = mid < pos["resistance"] * 0.995

        if not (tp_hit or stop_hit or max_hold or invalid):
            return None

        if stop_hit or invalid:
            reason, exit_price = ("stop_loss" if stop_hit else "invalidation"), mid
        elif tp_hit:
            reason, exit_price = "take_profit", pos["tp"]
        else:
            reason, exit_price = "max_hold", mid

        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": exit_price, "hold_s": ts - pos["opened_at"],
                      "pos_id": pos.get("pos_id")},
        )

    def _log_skip(self, symbol, reason, ts, mid, spread_bps):
        if self.decision_logger:
            self.decision_logger.log_skip(symbol, reason, timestamp=ts,
                                          mid=mid, spread_bps=spread_bps)
