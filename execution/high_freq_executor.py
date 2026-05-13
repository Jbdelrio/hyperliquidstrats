"""
high_freq_executor.py — Paper fill simulation + position lifecycle for S8.

Paper mode (default):
  BUY order fills when best_ask <= buy_price
  SELL order fills when best_bid >= sell_price
  Market closes at current mid (stops) or best_bid/ask (TP)

Live mode: not implemented — raise NotImplementedError.

No asyncio locks needed: all calls happen inside the single asyncio event loop.
"""
import csv
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger(__name__)

MAKER_REBATE_BPS = 0.3
TAKER_FEE_BPS   = 3.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class PendingOrder:
    order_id:    str
    symbol:      str
    side:        str    # "BUY" or "SELL"
    price:       float
    size_units:  float
    notional_usd: float
    placed_at:   float
    pair_id:     str    # ties BUY+SELL of same quote cycle


@dataclass
class OpenPosition:
    pos_id:       str
    symbol:       str
    side:         str
    size_units:   float
    notional_usd: float
    entry_price:  float
    tp_price:     float
    stop_price:   float
    max_hold_ts:  float
    entry_ts:     float


@dataclass
class FillResult:
    fill_id:     str
    symbol:      str
    side:        str
    price:       float
    size_units:  float
    notional_usd: float
    ts:          float
    order_id:    str


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class HighFreqExecutor:
    """
    Lifecycle:
      1. place_quotes(buy_price, sell_price, size, notional) → pair_id
      2. check_fills(symbol, best_bid, best_ask) → list[FillResult]
         on fill: cancels sibling, calls on_fill_cb(fill) → OpenPosition
      3. check_exits(symbol, mid, best_bid, best_ask) → close events
      4. cancel_quotes(symbol) / cancel_all()
    """

    def __init__(self, paper: bool = True,
                 trade_log: str = "logs/fills_v9.csv",
                 on_fill_cb: Optional[Callable] = None):
        if not paper:
            raise NotImplementedError("Live execution not implemented")

        self.paper = paper
        self.trade_log = trade_log
        self.on_fill_cb = on_fill_cb

        self._pending:  dict[str, PendingOrder]  = {}
        self._pairs:    dict[str, list[str]]      = {}   # pair_id → [buy_id, sell_id]
        self._positions: dict[str, OpenPosition] = {}

        Path(trade_log).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Place quotes
    # ------------------------------------------------------------------

    def place_quotes(self, symbol: str, buy_price: float, sell_price: float,
                     size_units: float, notional_usd: float) -> str:
        # One pair per symbol max
        if any(o.symbol == symbol for o in self._pending.values()):
            return ""

        pair_id = str(uuid.uuid4())[:8]
        buy_id  = f"b_{pair_id}"
        sell_id = f"s_{pair_id}"
        now = time.time()

        self._pending[buy_id] = PendingOrder(
            order_id=buy_id, symbol=symbol, side="BUY",
            price=buy_price, size_units=size_units, notional_usd=notional_usd,
            placed_at=now, pair_id=pair_id,
        )
        self._pending[sell_id] = PendingOrder(
            order_id=sell_id, symbol=symbol, side="SELL",
            price=sell_price, size_units=size_units, notional_usd=notional_usd,
            placed_at=now, pair_id=pair_id,
        )
        self._pairs[pair_id] = [buy_id, sell_id]

        log.debug("[QUOTE] %s buy=%.6f sell=%.6f notional=$%.0f",
                  symbol, buy_price, sell_price, notional_usd)
        return pair_id

    def symbols_with_pending(self) -> set[str]:
        return {o.symbol for o in self._pending.values()}

    def symbols_with_positions(self) -> set[str]:
        return {p.symbol for p in self._positions.values()}

    # ------------------------------------------------------------------
    # 2. Check fills (called on every book update)
    # ------------------------------------------------------------------

    def check_fills(self, symbol: str,
                    best_bid: float, best_ask: float) -> list[FillResult]:
        orders = [o for o in self._pending.values() if o.symbol == symbol]
        fills  = []

        for order in orders:
            if order.side == "BUY" and best_ask <= order.price:
                fill_price = min(order.price, best_ask)
                fill = self._make_fill(order, fill_price)
                fills.append(fill)
                self._open_position_from_fill(fill, order)

            elif order.side == "SELL" and best_bid >= order.price:
                fill_price = max(order.price, best_bid)
                fill = self._make_fill(order, fill_price)
                fills.append(fill)
                self._open_position_from_fill(fill, order)

        return fills

    def _make_fill(self, order: PendingOrder, price: float) -> FillResult:
        return FillResult(
            fill_id=str(uuid.uuid4())[:8],
            symbol=order.symbol, side=order.side, price=price,
            size_units=order.size_units, notional_usd=order.notional_usd,
            ts=time.time(), order_id=order.order_id,
        )

    def _open_position_from_fill(self, fill: FillResult, order: PendingOrder):
        # Cancel sibling
        siblings = self._pairs.pop(order.pair_id, [])
        for oid in siblings:
            if oid != order.order_id:
                self._pending.pop(oid, None)
        self._pending.pop(order.order_id, None)

        pos = OpenPosition(
            pos_id=fill.fill_id, symbol=fill.symbol, side=fill.side,
            size_units=fill.size_units, notional_usd=fill.notional_usd,
            entry_price=fill.price,
            tp_price=0.0, stop_price=0.0, max_hold_ts=0.0,  # set by on_fill_cb
            entry_ts=fill.ts,
        )
        self._positions[pos.pos_id] = pos

        if self.on_fill_cb:
            self.on_fill_cb(fill, pos)

        log.info("[FILL] %s %s @ %.6f notional=$%.0f",
                 fill.symbol, fill.side, fill.price, fill.notional_usd)

    def set_position_exits(self, pos_id: str,
                           tp_price: float, stop_price: float, max_hold_ts: float):
        pos = self._positions.get(pos_id)
        if pos:
            pos.tp_price    = tp_price
            pos.stop_price  = stop_price
            pos.max_hold_ts = max_hold_ts

    # ------------------------------------------------------------------
    # 3. Check exits
    # ------------------------------------------------------------------

    def check_exits(self, symbol: str, mid: float,
                    best_bid: float, best_ask: float
                    ) -> list[tuple["OpenPosition", float, str]]:
        now = time.time()
        to_close = []

        for pos in [p for p in self._positions.values() if p.symbol == symbol]:
            reason = exit_price = None

            if now >= pos.max_hold_ts and pos.max_hold_ts > 0:
                reason, exit_price = "max_hold", mid

            elif pos.side == "BUY":
                if mid <= pos.stop_price:
                    reason, exit_price = "stop_loss", best_bid
                elif pos.tp_price > 0 and mid >= pos.tp_price:
                    reason, exit_price = "take_profit", pos.tp_price

            elif pos.side == "SELL":
                if mid >= pos.stop_price:
                    reason, exit_price = "stop_loss", best_ask
                elif pos.tp_price > 0 and mid <= pos.tp_price:
                    reason, exit_price = "take_profit", pos.tp_price

            if reason:
                to_close.append((pos, exit_price, reason))

        return to_close

    def close_position(self, pos: "OpenPosition", exit_price: float,
                        reason: str, strategy: str = "") -> float:
        if pos.side == "BUY":
            gross = (exit_price - pos.entry_price) / pos.entry_price * pos.notional_usd
        else:
            gross = (pos.entry_price - exit_price) / pos.entry_price * pos.notional_usd

        maker_exit = reason == "take_profit"
        exit_fee = (-MAKER_REBATE_BPS if maker_exit else TAKER_FEE_BPS) * pos.notional_usd / 10_000
        entry_rebate = MAKER_REBATE_BPS * pos.notional_usd / 10_000
        net = gross - exit_fee + entry_rebate

        hold_s = time.time() - pos.entry_ts
        log.info("[CLOSE] %s %s entry=%.6f exit=%.6f net=$%.4f hold=%.0fs %s",
                 pos.symbol, pos.side, pos.entry_price, exit_price, net, hold_s, reason)

        self._positions.pop(pos.pos_id, None)
        self._log_trade(pos, exit_price, gross, exit_fee - entry_rebate, net, reason, hold_s, strategy)
        return net

    # ------------------------------------------------------------------
    # 4. Cancel helpers
    # ------------------------------------------------------------------

    def cancel_quotes(self, symbol: str) -> None:
        to_del = [oid for oid, o in self._pending.items() if o.symbol == symbol]
        for oid in to_del:
            self._pending.pop(oid, None)

    def cancel_all(self) -> None:
        self._pending.clear()
        self._pairs.clear()

    def close_all_market(self, mids: dict[str, float], reason: str = "emergency") -> float:
        self.cancel_all()
        total = 0.0
        for pos in list(self._positions.values()):
            mid = mids.get(pos.symbol, pos.entry_price)
            total += self.close_position(pos, mid, reason)
        return total

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def open_positions(self) -> list["OpenPosition"]:
        return list(self._positions.values())

    @property
    def pending_orders(self) -> list[PendingOrder]:
        return list(self._pending.values())

    # ------------------------------------------------------------------
    # CSV logging
    # ------------------------------------------------------------------

    def _log_trade(self, pos: "OpenPosition", exit_price: float,
                   gross: float, fee: float, net: float,
                   reason: str, hold_s: float, strategy: str = ""):
        try:
            write_hdr = not Path(self.trade_log).exists()
            with open(self.trade_log, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_hdr:
                    w.writerow(["ts", "symbol", "side", "notional",
                                 "entry", "exit", "gross", "fee", "net",
                                 "hold_s", "reason", "strategy"])
                w.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    pos.symbol, pos.side, round(pos.notional_usd, 2),
                    round(pos.entry_price, 8), round(exit_price, 8),
                    round(gross, 6), round(fee, 6), round(net, 6),
                    round(hold_s, 1), reason, strategy,
                ])
        except Exception as e:
            log.error("Trade log write failed: %s", e)
