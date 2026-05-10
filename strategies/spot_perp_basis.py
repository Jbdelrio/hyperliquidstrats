"""
spot_perp_basis.py — Spot/Perp basis scanner (perp-only proxy).

Detects abnormal basis between external spot price and Hyperliquid perp.
If no external spot feed is configured, runs as a pure scanner (no trades).
"""
import logging
import time
from collections import deque
from typing import Optional

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


class SpotPerpBasisStrategy(BaseStrategy):
    """
    Basis = 10000 × (perp_mid − spot) / spot  [bps]

    Positive basis  → perp expensive → short perp (PLACE_SELL)
    Negative basis  → perp cheap     → long perp  (PLACE_BUY)

    Without an external spot feed and trade_when_external_spot_missing=false
    the strategy only exposes calibration data (scanner mode).
    """

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        self._mid:       dict[str, Optional[float]] = {c: None for c in config.coins}
        self._basis_bps: dict[str, Optional[float]] = {c: None for c in config.coins}
        self._positions: dict[str, dict]             = {}
        self._last_mid_ts: dict[str, float]          = {}

    # ── BaseStrategy interface ───────────────────────────────────────────────

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            return None
        mid = (bid + ask) / 2
        self._mid[symbol] = mid
        self._last_mid_ts[symbol] = ts

        # Update live basis
        spot = self._get_spot(symbol)
        if spot:
            self._basis_bps[symbol] = (mid - spot) / spot * 10_000

        # Exit check
        if symbol in self._positions:
            dec = self._check_exit(symbol, mid, ts)
            if dec:
                return dec

        # Entry check
        return self._check_entry(symbol, bid, ask, mid, ts)

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p = self.config.params
        sl_pct      = p.get("stop_loss_pct", 0.015)
        tp_pct      = p.get("take_profit_pct", 0.010)
        max_hold_s  = int(p.get("max_hold_minutes", 240) * 60)

        stop = price * (1 + sl_pct) if side == "SELL" else price * (1 - sl_pct)
        tp   = price * (1 - tp_pct) if side == "SELL" else price * (1 + tp_pct)

        self._positions[symbol] = {
            "side":        side,
            "entry":       price,
            "stop":        stop,
            "tp":          tp,
            "opened_at":   ts,
            "max_hold_ts": ts + max_hold_s,
            "entry_basis": self._basis_bps.get(symbol),
            "pos_id":      pos_id,
        }
        return {"tp_price": tp, "stop_price": stop, "max_hold_seconds": max_hold_s}

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
        p = self.config.params
        spot = self._get_spot(symbol)
        mid  = self._mid.get(symbol)
        basis = self._basis_bps.get(symbol)
        return {
            "perp_mid":       round(mid,   6) if mid   else None,
            "spot_price":     round(spot,  6) if spot  else None,
            "basis_bps":      round(basis, 3) if basis else None,
            "basis_status":   self._basis_label(basis, p),
            "spot_available": spot is not None,
            "in_position":    symbol in self._positions,
            "mode":           "perp_only_basis_proxy",
        }

    def get_stats(self) -> dict:
        d = super().get_stats()
        d["open_positions_count"] = len(self._positions)
        return d

    # ── Internal ─────────────────────────────────────────────────────────────

    def _get_spot(self, symbol: str) -> Optional[float]:
        """Return external spot price if configured, else None."""
        ext = self.config.params.get("external_spot_prices", {})
        if isinstance(ext, dict):
            v = ext.get(symbol)
            if v is not None:
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
        return None

    def _check_entry(self, symbol: str, bid: float, ask: float,
                     mid: float, ts: float) -> Optional[StrategyDecision]:
        if symbol in self._positions:
            return None
        if len(self._positions) >= self.config.max_positions:
            return None

        p    = self.config.params
        spot = self._get_spot(symbol)

        if spot is None:
            if not p.get("trade_when_external_spot_missing", False):
                return None

        if spot is not None:
            basis_bps = (mid - spot) / spot * 10_000
        else:
            return None

        self._basis_bps[symbol] = basis_bps

        entry_thr    = p.get("basis_entry_bps", 20.0)
        max_basis    = p.get("max_basis_abs_bps", 200.0)
        min_edge_bps = p.get("min_expected_edge_bps", 8.0)

        if abs(basis_bps) > max_basis:
            return None  # data anomaly, skip

        spread_bps = (ask - bid) / mid * 10_000
        expected_edge = abs(basis_bps) - spread_bps - 3.5 - 2.0  # minus fee/slippage
        if expected_edge < min_edge_bps:
            return None

        notional  = self.compute_order_notional()
        max_hold_s = int(p.get("max_hold_minutes", 240) * 60)

        if basis_bps > entry_thr:
            side = "SELL"
            reason = f"basis_positive_short_perp basis={basis_bps:.1f}bps"
            sl_pct = p.get("stop_loss_pct", 0.015)
            tp_pct = p.get("take_profit_pct", 0.010)
            return StrategyDecision(
                action="PLACE_SELL", symbol=symbol, reason=reason,
                sell_price=bid, size=notional / max(bid, 1e-9),
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"basis_bps": basis_bps, "expected_edge": expected_edge,
                           "mode": "perp_only_basis_proxy"},
            )
        elif basis_bps < -entry_thr:
            side = "BUY"
            reason = f"basis_negative_long_perp basis={basis_bps:.1f}bps"
            return StrategyDecision(
                action="PLACE_BUY", symbol=symbol, reason=reason,
                buy_price=ask, size=notional / max(ask, 1e-9),
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"basis_bps": basis_bps, "expected_edge": expected_edge,
                           "mode": "perp_only_basis_proxy"},
            )
        return None

    def _check_exit(self, symbol: str, mid: float, ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        p           = self.config.params
        exit_thr    = p.get("basis_exit_bps", 5.0)
        side        = pos["side"]
        stop_hit    = (side == "SELL" and mid >= pos["stop"]) or \
                      (side == "BUY"  and mid <= pos["stop"])
        tp_hit      = (side == "SELL" and mid <= pos["tp"]) or \
                      (side == "BUY"  and mid >= pos["tp"])
        max_hold    = ts >= pos["max_hold_ts"]

        # Basis convergence exit
        spot = self._get_spot(symbol)
        basis_converged = False
        if spot is not None:
            cur_basis = (mid - spot) / spot * 10_000
            self._basis_bps[symbol] = cur_basis
            basis_converged = abs(cur_basis) < exit_thr

        if not (stop_hit or tp_hit or max_hold or basis_converged):
            return None

        if stop_hit:         reason = "stop_loss"
        elif tp_hit:         reason = "take_profit"
        elif basis_converged: reason = "basis_converged"
        else:                reason = "max_hold"

        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "hold_s": ts - pos["opened_at"],
                      "pos_id": pos.get("pos_id"), "basis_bps": self._basis_bps.get(symbol)},
        )

    def _basis_label(self, basis_bps, p) -> str:
        if basis_bps is None:
            return "no_data"
        entry = p.get("basis_entry_bps", 20.0)
        exit_ = p.get("basis_exit_bps", 5.0)
        if abs(basis_bps) > entry:
            return "tradeable"
        if abs(basis_bps) < exit_:
            return "converged"
        return "normal"
