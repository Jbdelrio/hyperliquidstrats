"""
high_freq_executor.py — Paper fill simulation + position lifecycle for S8.

Paper mode (default):
  BUY order fills when best_ask <= buy_price
  SELL order fills when best_bid >= sell_price
  Market closes at current mid (stops) or best_bid/ask (TP)

Realistic-fill upgrades (Phase 1):
  - Order type tracked per order: MAKER_SIM or TAKER_SIM.
  - Simulated latency for TAKER orders (paper_latency_ms, default 150ms).
  - Order expiry (max_pending_seconds, default 30s TAKER / 120s MAKER).
  - Dynamic slippage applied to TAKER fills based on spread + notional.
  - slippage_bps written to fills_v9.csv for downstream analysis.

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

# Phase 1 defaults — overridable via the `config` dict passed to the executor
_DEFAULT_LATENCY_MS         = 150.0
_DEFAULT_MAX_PENDING_TAKER  = 30.0
_DEFAULT_MAX_PENDING_MAKER  = 120.0
_DEFAULT_BASE_SLIPPAGE_BPS  = 2.0


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
    # Phase 1 additions
    order_type:         str   = "MAKER_SIM"         # "MAKER_SIM" or "TAKER_SIM"
    max_pending_seconds: float = _DEFAULT_MAX_PENDING_MAKER
    expired:            bool   = False
    # Phase 6: trace through to orders_v9.csv
    signal_id:    str = ""
    strategy:     str = ""


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
    # Phase 1: slippage applied (in bps) for analytics
    slippage_bps: float = 0.0


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
      4. expire_stale_orders(now) → list[PendingOrder] of expired orders
      5. cancel_quotes(symbol) / cancel_all()
    """

    def __init__(self, paper: bool = True,
                 trade_log: str = "logs/fills_v9.csv",
                 on_fill_cb: Optional[Callable] = None,
                 config: Optional[dict] = None,
                 orders_log: str = "logs/orders_v9.csv"):
        if not paper:
            raise NotImplementedError("Live execution not implemented")

        self.paper = paper
        self.trade_log = trade_log
        self.orders_log = orders_log
        self.on_fill_cb = on_fill_cb

        # Phase 1: read realistic-fill knobs from config (with safe defaults)
        cfg = config or {}
        self._latency_ms        = float(cfg.get("paper_latency_ms", _DEFAULT_LATENCY_MS))
        self._max_pending_taker = float(cfg.get("max_pending_seconds_taker",
                                                _DEFAULT_MAX_PENDING_TAKER))
        self._max_pending_maker = float(cfg.get("max_pending_seconds_maker",
                                                _DEFAULT_MAX_PENDING_MAKER))
        self._base_slippage_bps = float(cfg.get("base_slippage_bps",
                                                _DEFAULT_BASE_SLIPPAGE_BPS))

        self._pending:  dict[str, PendingOrder]  = {}
        self._pairs:    dict[str, list[str]]      = {}   # pair_id → [buy_id, sell_id]
        self._positions: dict[str, OpenPosition] = {}

        Path(trade_log).parent.mkdir(parents=True, exist_ok=True)
        Path(orders_log).parent.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # 1. Place quotes
    # ------------------------------------------------------------------

    def place_quotes(self, symbol: str, buy_price: float, sell_price: float,
                     size_units: float, notional_usd: float,
                     order_type: str = "MAKER_SIM",
                     signal_id: str = "",
                     strategy: str = "") -> str:
        """
        order_type: "MAKER_SIM" (S8EMS-style quoting, no latency penalty) or
                    "TAKER_SIM" (directional PLACE_BUY/SELL, latency + slippage).
        signal_id / strategy: optional trace for orders_v9.csv.
        """
        # One pair per symbol max
        if any(o.symbol == symbol for o in self._pending.values()):
            return ""

        pair_id = str(uuid.uuid4())[:8]
        buy_id  = f"b_{pair_id}"
        sell_id = f"s_{pair_id}"
        now = time.time()

        max_pending = (self._max_pending_taker if order_type == "TAKER_SIM"
                       else self._max_pending_maker)

        self._pending[buy_id] = PendingOrder(
            order_id=buy_id, symbol=symbol, side="BUY",
            price=buy_price, size_units=size_units, notional_usd=notional_usd,
            placed_at=now, pair_id=pair_id,
            order_type=order_type, max_pending_seconds=max_pending,
            signal_id=signal_id, strategy=strategy,
        )
        self._pending[sell_id] = PendingOrder(
            order_id=sell_id, symbol=symbol, side="SELL",
            price=sell_price, size_units=size_units, notional_usd=notional_usd,
            placed_at=now, pair_id=pair_id,
            order_type=order_type, max_pending_seconds=max_pending,
            signal_id=signal_id, strategy=strategy,
        )
        self._pairs[pair_id] = [buy_id, sell_id]

        log.debug("[QUOTE] %s buy=%.6f sell=%.6f notional=$%.0f type=%s",
                  symbol, buy_price, sell_price, notional_usd, order_type)
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
        now = time.time()
        latency_s = self._latency_ms / 1000.0

        for order in orders:
            # Phase 1: TAKER orders only become active after latency elapses
            if order.order_type == "TAKER_SIM":
                if (now - order.placed_at) < latency_s:
                    continue

            if order.side == "BUY" and best_ask <= order.price:
                fill_price, slip_bps = self._apply_slippage(
                    order, best_ask, best_bid, best_ask)
                fill = self._make_fill(order, fill_price, slip_bps)
                fills.append(fill)
                self._open_position_from_fill(fill, order)

            elif order.side == "SELL" and best_bid >= order.price:
                fill_price, slip_bps = self._apply_slippage(
                    order, best_bid, best_bid, best_ask)
                fill = self._make_fill(order, fill_price, slip_bps)
                fills.append(fill)
                self._open_position_from_fill(fill, order)

        return fills

    def _apply_slippage(self, order: PendingOrder, base_price: float,
                        best_bid: float, best_ask: float) -> tuple[float, float]:
        """
        Apply dynamic slippage to TAKER fills.
        MAKER fills keep the limit price (0 bps slippage).

        slippage_bps = base_slippage_bps
                     + spread_bps * 0.5
                     + clamp(notional / 50_000, 0, 5.0)
        BUY  → fill above base_price (worse for buyer)
        SELL → fill below base_price (worse for seller)
        """
        if order.order_type != "TAKER_SIM":
            return base_price, 0.0

        mid = (best_bid + best_ask) / 2.0 if best_bid > 0 and best_ask > 0 else base_price
        spread_bps = ((best_ask - best_bid) / mid * 10_000.0) if mid > 0 else 0.0
        size_adj   = max(0.0, min(5.0, order.notional_usd / 50_000.0))
        slip_bps   = self._base_slippage_bps + 0.5 * spread_bps + size_adj
        slip_frac  = slip_bps / 10_000.0

        if order.side == "BUY":
            fill_price = base_price * (1.0 + slip_frac)
        else:
            fill_price = base_price * (1.0 - slip_frac)
        return fill_price, slip_bps

    def _make_fill(self, order: PendingOrder, price: float,
                   slippage_bps: float = 0.0) -> FillResult:
        return FillResult(
            fill_id=str(uuid.uuid4())[:8],
            symbol=order.symbol, side=order.side, price=price,
            size_units=order.size_units, notional_usd=order.notional_usd,
            ts=time.time(), order_id=order.order_id,
            slippage_bps=slippage_bps,
        )

    def _open_position_from_fill(self, fill: FillResult, order: PendingOrder):
        # Cancel sibling
        siblings = self._pairs.pop(order.pair_id, [])
        sibling_orders: list[PendingOrder] = []
        for oid in siblings:
            if oid != order.order_id:
                sib = self._pending.pop(oid, None)
                if sib is not None:
                    sibling_orders.append(sib)
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

        # Phase 6: orders_v9.csv logging — FILL row for the filled order,
        # CANCEL rows for the auto-cancelled siblings.
        self._log_order_event(
            order=order, status="FILL", reason="fill",
            notional_filled=fill.notional_usd, fill_ratio=1.0,
            slippage_bps=fill.slippage_bps,
            fee_bps=(MAKER_REBATE_BPS if order.order_type == "MAKER_SIM"
                     else TAKER_FEE_BPS),
        )
        for sib in sibling_orders:
            self._log_order_event(
                order=sib, status="CANCEL", reason="sibling_filled",
                notional_filled=0.0, fill_ratio=0.0,
                slippage_bps=0.0, fee_bps=0.0,
            )

        log.info("[FILL] %s %s @ %.6f notional=$%.0f slip=%.1fbps",
                 fill.symbol, fill.side, fill.price, fill.notional_usd,
                 fill.slippage_bps)

    def set_position_exits(self, pos_id: str,
                           tp_price: float, stop_price: float, max_hold_ts: float):
        pos = self._positions.get(pos_id)
        if pos:
            pos.tp_price    = tp_price
            pos.stop_price  = stop_price
            pos.max_hold_ts = max_hold_ts

    # ------------------------------------------------------------------
    # 2b. Expire stale orders (Phase 1)
    # ------------------------------------------------------------------

    def expire_stale_orders(self, now: float) -> list[PendingOrder]:
        """
        Remove orders that have aged beyond max_pending_seconds.
        Returns the list of expired PendingOrder objects so the caller
        (engine) can release any reserved capital on the ledger.

        Pair siblings are expired together to keep accounting consistent.
        """
        expired_pairs: set[str] = set()
        for order in self._pending.values():
            age = now - order.placed_at
            if age >= order.max_pending_seconds:
                expired_pairs.add(order.pair_id)

        expired_orders: list[PendingOrder] = []
        for pair_id in expired_pairs:
            order_ids = self._pairs.pop(pair_id, [])
            for oid in order_ids:
                o = self._pending.pop(oid, None)
                if o is not None:
                    o.expired = True
                    expired_orders.append(o)
            # Catch any orders not in the pair index (shouldn't happen, but safe)
            for oid in list(self._pending.keys()):
                o = self._pending[oid]
                if o.pair_id == pair_id:
                    self._pending.pop(oid, None)
                    o.expired = True
                    expired_orders.append(o)

        if expired_orders:
            for o in expired_orders:
                log.debug("[EXPIRE] %s %s @ %.6f age=%.1fs type=%s",
                          o.symbol, o.side, o.price, now - o.placed_at,
                          o.order_type)
                self._log_order_event(
                    order=o, status="EXPIRE",
                    reason=f"age={now - o.placed_at:.1f}s",
                    notional_filled=0.0, fill_ratio=0.0,
                    slippage_bps=0.0, fee_bps=0.0,
                    queue_wait_s=now - o.placed_at,
                )
        return expired_orders

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
        self._log_trade(pos, exit_price, gross, exit_fee - entry_rebate, net,
                        reason, hold_s, strategy)
        return net

    # ------------------------------------------------------------------
    # 4. Cancel helpers
    # ------------------------------------------------------------------

    def cancel_quotes(self, symbol: str) -> None:
        to_del = [(oid, o) for oid, o in self._pending.items() if o.symbol == symbol]
        for oid, o in to_del:
            self._pending.pop(oid, None)
            try:
                self._log_order_event(
                    order=o, status="CANCEL", reason="cancel_quotes",
                    notional_filled=0.0, fill_ratio=0.0,
                    slippage_bps=0.0, fee_bps=0.0,
                )
            except Exception:
                pass

    def cancel_all(self) -> None:
        for o in list(self._pending.values()):
            try:
                self._log_order_event(
                    order=o, status="CANCEL", reason="cancel_all",
                    notional_filled=0.0, fill_ratio=0.0,
                    slippage_bps=0.0, fee_bps=0.0,
                )
            except Exception:
                pass
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

    # ------------------------------------------------------------------
    # Phase 6: orders_v9.csv logging
    # ------------------------------------------------------------------

    def _log_order_event(self, *, order: PendingOrder, status: str,
                          reason: str,
                          notional_filled: float = 0.0,
                          fill_ratio: float = 0.0,
                          slippage_bps: float = 0.0,
                          fee_bps: float = 0.0,
                          queue_wait_s: float = 0.0) -> None:
        """Append one row to orders_v9.csv. Best-effort; never raises."""
        try:
            write_hdr = not Path(self.orders_log).exists()
            with open(self.orders_log, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_hdr:
                    w.writerow([
                        "timestamp", "signal_id", "strategy", "symbol", "side",
                        "order_type", "limit_price",
                        "notional_requested", "notional_filled", "fill_ratio",
                        "status", "reason",
                        "queue_wait_s", "slippage_bps", "fee_bps",
                    ])
                w.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    order.signal_id, order.strategy, order.symbol, order.side,
                    order.order_type, round(order.price, 8),
                    round(order.notional_usd, 4),
                    round(notional_filled, 4),
                    round(fill_ratio, 4),
                    status, reason,
                    round(queue_wait_s, 3),
                    round(slippage_bps, 3),
                    round(fee_bps, 3),
                ])
        except Exception as e:
            log.debug("Order log write failed: %s", e)

    def _log_trade(self, pos: "OpenPosition", exit_price: float,
                   gross: float, fee: float, net: float,
                   reason: str, hold_s: float, strategy: str = ""):
        try:
            write_hdr = not Path(self.trade_log).exists()
            with open(self.trade_log, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if write_hdr:
                    # NB: slippage_bps column added (Phase 1) — preserves all
                    # existing columns to avoid breaking downstream readers.
                    w.writerow(["ts", "symbol", "side", "notional",
                                 "entry", "exit", "gross", "fee", "net",
                                 "hold_s", "reason", "strategy", "slippage_bps"])
                # Note: exit-side fills (this close call) do not carry an
                # explicit slippage tag — fees already model the round-trip
                # exit cost. We log "" (blank) so the column exists for
                # downstream tools without misrepresenting close slippage.
                w.writerow([
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    pos.symbol, pos.side, round(pos.notional_usd, 2),
                    round(pos.entry_price, 8), round(exit_price, 8),
                    round(gross, 6), round(fee, 6), round(net, 6),
                    round(hold_s, 1), reason, strategy, "",
                ])
        except Exception as e:
            log.error("Trade log write failed: %s", e)
