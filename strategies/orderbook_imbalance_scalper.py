"""
orderbook_imbalance_scalper.py — Directional scalper driven by order-book imbalance.

imbalance = (bid_depth − ask_depth) / (bid_depth + ask_depth)  ∈ [−1, +1]

If imbalance > imbalance_entry for min_persistence_updates consecutive books → PLACE_BUY
If imbalance < −imbalance_entry                                              → PLACE_SELL

Uses book.imbalance(n_levels) which the Hyperliquid OrderBook already exposes.
"""
import logging
from collections import deque
from typing import Optional

from strategies.base_strategy import BarData, BaseStrategy, StrategyConfig, StrategyDecision

log = logging.getLogger(__name__)


class OrderBookImbalanceScalper(BaseStrategy):
    """
    Pure order-flow scalper.  Entry driven by sustained book imbalance;
    exit driven by imbalance reversal, stop-loss, take-profit, or max hold.
    """

    def __init__(self, config: StrategyConfig, logger=None, decision_logger=None):
        super().__init__(config, logger, decision_logger)
        p = config.params
        n_hist = max(int(p.get("min_persistence_updates", 3)) + 1, 10)
        self._imb_hist:   dict[str, deque] = {c: deque(maxlen=n_hist) for c in config.coins}
        self._positions:  dict[str, dict]  = {}
        self._last_mid:   dict[str, Optional[float]] = {c: None for c in config.coins}

    # ── BaseStrategy interface ───────────────────────────────────────────────

    def on_orderbook_update(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        bid = book.best_bid
        ask = book.best_ask
        if bid is None or ask is None:
            return None
        mid = (bid + ask) / 2
        self._last_mid[symbol] = mid

        p       = self.config.params
        n_lev   = int(p.get("imbalance_levels", 5))
        imb     = book.imbalance(n_lev)
        self._imb_hist[symbol].append(imb)

        # Exit check first (tighter loop)
        if symbol in self._positions:
            dec = self._check_exit(symbol, mid, imb, ts)
            if dec:
                return dec

        return self._check_entry(symbol, bid, ask, mid, imb, ts)

    def on_trade_update(self, symbol: str, trade, ts: float) -> None:
        pass

    def on_bar_minute(self, symbol: str, bar: BarData, ts: float) -> Optional[StrategyDecision]:
        return None

    def on_fill(self, symbol: str, side: str, price: float, size: float,
                ts: float, pos_id: str = "") -> Optional[dict]:
        p          = self.config.params
        sl_pct     = p.get("stop_loss_pct", 0.004)
        tp_pct     = p.get("take_profit_pct", 0.003)
        max_hold_s = int(p.get("max_hold_seconds", 120))

        stop = price * (1 + sl_pct) if side == "SELL" else price * (1 - sl_pct)
        tp   = price * (1 - tp_pct) if side == "SELL" else price * (1 + tp_pct)

        self._positions[symbol] = {
            "side":        side,
            "entry":       price,
            "stop":        stop,
            "tp":          tp,
            "opened_at":   ts,
            "max_hold_ts": ts + max_hold_s,
            "pos_id":      pos_id,
        }
        return {"tp_price": tp, "stop_price": stop, "max_hold_seconds": max_hold_s}

    def check_position_exits(self, symbol: str, book, ts: float) -> Optional[StrategyDecision]:
        if symbol not in self._positions:
            return None
        mid = getattr(book, "mid", None)
        if mid is None:
            bid, ask = book.best_bid, book.best_ask
            if bid is None or ask is None:
                return None
            mid = (bid + ask) / 2
        p     = self.config.params
        n_lev = int(p.get("imbalance_levels", 5))
        imb   = book.imbalance(n_lev)
        return self._check_exit(symbol, mid, imb, ts)

    def on_position_closed(self, symbol: str, pnl_net: float, exit_reason: str) -> None:
        self._positions.pop(symbol, None)
        super().on_position_closed(symbol, pnl_net, exit_reason)

    def get_calibration_data(self, symbol: str) -> dict:
        p    = self.config.params
        hist = list(self._imb_hist.get(symbol, []))
        cur  = hist[-1] if hist else None
        avg  = sum(hist) / len(hist) if hist else None
        thr  = p.get("imbalance_entry_threshold", 0.30)
        return {
            "current_imbalance":   round(cur, 4)  if cur  is not None else None,
            "avg_imbalance":       round(avg, 4)  if avg  is not None else None,
            "entry_threshold":     thr,
            "persistence_needed":  int(p.get("min_persistence_updates", 3)),
            "samples_collected":   len(hist),
            "in_position":         symbol in self._positions,
            "signal":              self._signal_label(cur, thr),
        }

    def get_stats(self) -> dict:
        d = super().get_stats()
        d["open_positions_count"] = len(self._positions)
        return d

    # ── Internal ─────────────────────────────────────────────────────────────

    def _check_entry(self, symbol: str, bid: float, ask: float,
                     mid: float, imb: float, ts: float) -> Optional[StrategyDecision]:
        if symbol in self._positions:
            return None
        if len(self._positions) >= self.config.max_positions:
            return None

        p        = self.config.params
        thr      = p.get("imbalance_entry_threshold", 0.30)
        persist  = int(p.get("min_persistence_updates", 3))
        hist     = list(self._imb_hist[symbol])

        if len(hist) < persist:
            return None

        recent = hist[-persist:]
        notional  = self.compute_order_notional()
        max_hold_s = int(p.get("max_hold_seconds", 120))

        if all(x > thr for x in recent):
            reason = f"obimb_buy imb={imb:.3f} (>{thr})"
            return StrategyDecision(
                action="PLACE_BUY", symbol=symbol, reason=reason,
                buy_price=ask, size=notional / max(ask, 1e-9),
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"imbalance": imb, "threshold": thr, "persist": persist},
            )

        if all(x < -thr for x in recent):
            reason = f"obimb_sell imb={imb:.3f} (<-{thr})"
            return StrategyDecision(
                action="PLACE_SELL", symbol=symbol, reason=reason,
                sell_price=bid, size=notional / max(bid, 1e-9),
                notional_usd=notional, max_hold_seconds=max_hold_s,
                metadata={"imbalance": imb, "threshold": thr, "persist": persist},
            )

        return None

    def _check_exit(self, symbol: str, mid: float, imb: float,
                    ts: float) -> Optional[StrategyDecision]:
        pos = self._positions.get(symbol)
        if pos is None:
            return None

        p    = self.config.params
        side = pos["side"]
        thr  = p.get("imbalance_exit_threshold", 0.05)

        stop_h = (side == "SELL" and mid >= pos["stop"]) or (side == "BUY" and mid <= pos["stop"])
        tp_h   = (side == "SELL" and mid <= pos["tp"])   or (side == "BUY" and mid >= pos["tp"])
        max_h  = ts >= pos["max_hold_ts"]
        # Imbalance flipped: book pressure reversed
        imb_rev = (side == "BUY" and imb < -thr) or (side == "SELL" and imb > thr)

        if not (stop_h or tp_h or max_h or imb_rev):
            return None

        if stop_h:    reason = "stop_loss"
        elif tp_h:    reason = "take_profit"
        elif imb_rev: reason = "imbalance_reversed"
        else:         reason = "max_hold"

        return StrategyDecision(
            action="CLOSE", symbol=symbol, reason=reason,
            metadata={"exit_price": mid, "imbalance": imb,
                      "hold_s": ts - pos["opened_at"],
                      "pos_id": pos.get("pos_id")},
        )

    def _signal_label(self, imb, thr) -> str:
        if imb is None:
            return "no_data"
        if imb > thr:
            return "buy_pressure"
        if imb < -thr:
            return "sell_pressure"
        return "neutral"
