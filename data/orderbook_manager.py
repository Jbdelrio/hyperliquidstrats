"""
orderbook_manager.py — Async WebSocket L2 orderbook + trade stream for Hyperliquid.

Subscribes to:
  • l2Book   per symbol  → maintains OrderBook, feeds book_queue
  • trades   per symbol  → maintains TradesBuffer, feeds trade_queue

Rate-limiting: subscription_delay_s between each subscribe call.
Reconnect: exponential back-off 1s → 64s.

Hardening (Phase 1 audit) — tracks per-message latency, validates books
(sorted, non-crossed, positive prices/sizes), keeps trades even when the
book isn't ready yet, and surfaces queue drops as counters + rate-limited
warnings instead of silent passes. `health_snapshot()` exposes all this
to the dashboard / audit script.
"""
import asyncio
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

import numpy as np
import websockets

from data.trades_buffer import Trade, TradesBuffer

log = logging.getLogger(__name__)

HL_WS_URL = "wss://api.hyperliquid.xyz/ws"

# Rate limit on noisy warnings (one log line per key per N seconds).
_WARN_INTERVAL_S = 30.0


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OrderBook:
    symbol: str
    bids: list[tuple[float, float]]   # [(price, size), ...] best-first (descending)
    asks: list[tuple[float, float]]   # [(price, size), ...] best-first (ascending)
    timestamp: float                  # exchange ts in seconds

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2
        return None

    @property
    def spread_bps(self) -> Optional[float]:
        if self.best_bid and self.best_ask and self.mid:
            return (self.best_ask - self.best_bid) / self.mid * 10_000
        return None

    def imbalance(self, n_levels: int = 5) -> float:
        bid_depth = sum(sz for _, sz in self.bids[:n_levels])
        ask_depth = sum(sz for _, sz in self.asks[:n_levels])
        total = bid_depth + ask_depth
        return (bid_depth - ask_depth) / total if total > 0 else 0.0


@dataclass
class BookUpdate:
    symbol: str
    book: OrderBook
    timestamp: float


@dataclass
class TradeEvent:
    symbol: str
    price: float
    size: float
    volume_usd: float
    side: str                       # "B" or "A"
    best_bid: Optional[float]       # may be None if book not ready
    best_ask: Optional[float]
    timestamp: float                # exchange ts
    recv_ts: float = 0.0            # local receive ts
    latency_ms: float = 0.0


# ---------------------------------------------------------------------------
# Per-symbol stats
# ---------------------------------------------------------------------------

@dataclass
class _SymStats:
    book_updates: int = 0
    trade_events: int = 0
    last_book_ts: float = 0.0          # exchange ts
    last_trade_ts: float = 0.0
    last_book_recv_ts: float = 0.0     # local ts (for stream-rate)
    last_trade_recv_ts: float = 0.0
    invalid_books: int = 0
    crossed_books: int = 0
    latency_samples: deque = field(default_factory=lambda: deque(maxlen=2000))
    spread_samples: deque = field(default_factory=lambda: deque(maxlen=2000))
    book_update_history: deque = field(default_factory=lambda: deque(maxlen=2000))
    trade_history: deque = field(default_factory=lambda: deque(maxlen=2000))
    current_mid: Optional[float] = None
    current_spread_bps: Optional[float] = None


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class OrderbookManager:
    """
    Maintains live L2 books for all symbols via a single WebSocket connection.

    Usage:
        obm = OrderbookManager(symbols)
        await obm.connect()
        async for update in obm.stream_orderbook_updates():
            ...
    """

    def __init__(self, symbols: list[str],
                 subscription_delay_s: float = 0.15,
                 ws_url: str = HL_WS_URL,
                 stale_book_s: float = 5.0,
                 stale_trade_s: float = 30.0):
        self.symbols = [s.upper() for s in symbols]
        self.sub_delay = subscription_delay_s
        self.ws_url = ws_url
        self.stale_book_s = float(stale_book_s)
        self.stale_trade_s = float(stale_trade_s)

        self._books: dict[str, OrderBook] = {}
        self._trades: dict[str, TradesBuffer] = {s: TradesBuffer() for s in self.symbols}

        # Per-symbol stats
        self._stats: dict[str, _SymStats] = {s: _SymStats() for s in self.symbols}
        # Rate-limited warn tracker
        self._last_warn_ts: dict[str, float] = defaultdict(float)

        # Minute-return tracking (for HAR-RV)
        self._min_open: dict[str, float] = {}
        self._min_start_ts: dict[str, float] = {}
        self._minute_returns: dict[str, Optional[float]] = {}

        # Async queues
        self._book_q: asyncio.Queue = asyncio.Queue(maxsize=50_000)
        self._trade_q: asyncio.Queue = asyncio.Queue(maxsize=50_000)

        # Global counters
        self.reconnections: int = 0
        self.book_updates_count: int = 0
        self.trade_events_count: int = 0
        self.dropped_book_updates_count: int = 0
        self.dropped_trade_events_count: int = 0
        self.json_parse_errors_count: int = 0
        self.invalid_book_count: int = 0
        self.crossed_book_count: int = 0

        self._running: bool = False
        self._ws_task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Start background WebSocket task and wait for first books."""
        self._running = True
        self._ws_task = asyncio.create_task(self._ws_loop())
        # Allow time for initial L2 snapshots
        await asyncio.sleep(3.0)
        log.info("OrderbookManager connected. Books ready: %d/%d",
                 len(self._books), len(self.symbols))

    async def stop(self) -> None:
        self._running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------
    # Async generators (consumed by engine loops)
    # ------------------------------------------------------------------

    async def stream_orderbook_updates(self) -> AsyncGenerator[BookUpdate, None]:
        while self._running:
            try:
                update = await asyncio.wait_for(self._book_q.get(), timeout=5.0)
                yield update
            except asyncio.TimeoutError:
                continue

    async def stream_trades(self) -> AsyncGenerator[TradeEvent, None]:
        while self._running:
            try:
                event = await asyncio.wait_for(self._trade_q.get(), timeout=5.0)
                yield event
            except asyncio.TimeoutError:
                continue

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_book(self, symbol: str) -> Optional[OrderBook]:
        return self._books.get(symbol.upper())

    def get_mid(self, symbol: str) -> Optional[float]:
        book = self._books.get(symbol.upper())
        return book.mid if book else None

    def get_trades(self, symbol: str, seconds: float = 30.0) -> list[Trade]:
        buf = self._trades.get(symbol.upper())
        return buf.get_recent(seconds) if buf else []

    def get_vwap(self, symbol: str, seconds: float = 30.0) -> Optional[float]:
        buf = self._trades.get(symbol.upper())
        return buf.get_vwap(seconds) if buf else None

    def get_minute_return(self, symbol: str) -> Optional[float]:
        """Consume the latest completed-minute log-return (or None)."""
        ret = self._minute_returns.pop(symbol.upper(), None)
        return ret

    def is_stale(self, symbol: str, max_age_s: float = 5.0) -> bool:
        book = self._books.get(symbol.upper())
        if not book:
            return True
        return (time.time() - book.timestamp) > max_age_s

    # ------------------------------------------------------------------
    # Health snapshot — consumed by audit + dashboard
    # ------------------------------------------------------------------

    def health_snapshot(self) -> dict:
        """Return a dict with global + per-symbol health metrics."""
        now = time.time()
        per_symbol = {}
        any_stale = False
        any_invalid = False
        for sym, st in self._stats.items():
            last_book_age = (now - st.last_book_ts) if st.last_book_ts > 0 else float("inf")
            last_trade_age = (now - st.last_trade_ts) if st.last_trade_ts > 0 else float("inf")
            # Stream rates (last 60 s window from history)
            cutoff = now - 60.0
            bu_rate = sum(1 for ts in st.book_update_history if ts >= cutoff) / 60.0
            tr_rate = sum(1 for ts in st.trade_history if ts >= cutoff) / 60.0
            # Latency / spread aggregates
            lats = list(st.latency_samples)
            sps = list(st.spread_samples)
            lat_mean = float(np.mean(lats)) if lats else float("nan")
            lat_p95 = float(np.percentile(lats, 95)) if len(lats) >= 20 else float("nan")
            lat_max = float(np.max(lats)) if lats else float("nan")
            spread_mean = float(np.mean(sps)) if sps else float("nan")
            spread_p95 = float(np.percentile(sps, 95)) if len(sps) >= 20 else float("nan")
            spread_max = float(np.max(sps)) if sps else float("nan")
            spread_min = float(np.min(sps)) if sps else float("nan")
            is_book_stale = last_book_age > self.stale_book_s
            is_trade_stale = last_trade_age > self.stale_trade_s
            if is_book_stale:
                any_stale = True
            if st.invalid_books or st.crossed_books:
                any_invalid = True
            per_symbol[sym] = {
                "book_updates": st.book_updates,
                "trade_events": st.trade_events,
                "book_updates_per_sec": bu_rate,
                "trades_per_sec": tr_rate,
                "last_book_ts": st.last_book_ts,
                "last_trade_ts": st.last_trade_ts,
                "last_book_age_s": last_book_age,
                "last_trade_age_s": last_trade_age,
                "is_book_stale": is_book_stale,
                "is_trade_stale": is_trade_stale,
                "avg_latency_ms": lat_mean,
                "p95_latency_ms": lat_p95,
                "max_latency_ms": lat_max,
                "spread_bps_mean": spread_mean,
                "spread_bps_p95": spread_p95,
                "spread_bps_max": spread_max,
                "spread_bps_min": spread_min,
                "current_mid": st.current_mid,
                "current_spread_bps": st.current_spread_bps,
                "invalid_books": st.invalid_books,
                "crossed_books": st.crossed_books,
            }
        return {
            "ts": now,
            "running": self._running,
            "symbols": list(self.symbols),
            "reconnections": self.reconnections,
            "book_updates_count": self.book_updates_count,
            "trade_events_count": self.trade_events_count,
            "dropped_book_updates_count": self.dropped_book_updates_count,
            "dropped_trade_events_count": self.dropped_trade_events_count,
            "json_parse_errors_count": self.json_parse_errors_count,
            "invalid_book_count": self.invalid_book_count,
            "crossed_book_count": self.crossed_book_count,
            "queue_drops": (self.dropped_book_updates_count
                            + self.dropped_trade_events_count),
            "any_stale_book": any_stale,
            "any_invalid_book": any_invalid,
            "per_symbol": per_symbol,
        }

    # ------------------------------------------------------------------
    # WebSocket loop
    # ------------------------------------------------------------------

    async def _ws_loop(self) -> None:
        backoff = 1
        while self._running:
            try:
                await self._run_ws()
                backoff = 1
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                self.reconnections += 1
                log.warning("WS error (%s). Reconnect in %ds (attempt %d)",
                            e, backoff, self.reconnections)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 64)

    async def _run_ws(self) -> None:
        async with websockets.connect(
            self.ws_url,
            ping_interval=20,
            ping_timeout=10,
            max_size=10 * 1024 * 1024,   # 10 MB
        ) as ws:
            log.info("WS connected. Subscribing to %d symbols...", len(self.symbols))

            # Subscribe with rate-limiting delay between each call
            for sym in self.symbols:
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "l2Book", "coin": sym},
                }))
                await asyncio.sleep(self.sub_delay)
                await ws.send(json.dumps({
                    "method": "subscribe",
                    "subscription": {"type": "trades", "coin": sym},
                }))
                await asyncio.sleep(self.sub_delay)

            log.info("All subscriptions sent.")

            async for raw in ws:
                if not self._running:
                    return
                try:
                    self._dispatch(json.loads(raw))
                except json.JSONDecodeError:
                    self.json_parse_errors_count += 1
                    self._warn_once("json_parse",
                                    "WS JSON parse error (count=%d)",
                                    self.json_parse_errors_count)
                except Exception as e:
                    log.debug("Message error: %s", e)

    def _dispatch(self, msg: dict) -> None:
        channel = msg.get("channel")
        data    = msg.get("data")
        if not data:
            return
        if channel == "l2Book":
            self._on_l2book(data)
        elif channel == "trades":
            self._on_trades(data)

    # ------------------------------------------------------------------
    # l2Book parser
    # ------------------------------------------------------------------

    def _on_l2book(self, data: dict) -> None:
        symbol = data.get("coin", "").upper()
        if symbol not in self.symbols:
            return
        st = self._stats[symbol]

        levels = data.get("levels", [[], []])
        ts_ms  = data.get("time", time.time() * 1000)
        ex_ts  = ts_ms / 1000.0
        recv_ts = time.time()
        latency_ms = max(0.0, (recv_ts - ex_ts) * 1000.0)

        try:
            bids = [(float(l["px"]), float(l["sz"])) for l in levels[0]]
            asks = [(float(l["px"]), float(l["sz"])) for l in levels[1]]
        except (KeyError, ValueError, TypeError):
            st.invalid_books += 1
            self.invalid_book_count += 1
            self._warn_once(f"parse_book:{symbol}",
                            "Invalid book payload for %s (count=%d)",
                            symbol, st.invalid_books)
            return

        # ---- Validation -------------------------------------------------
        ok, why = self._validate_levels(bids, asks)
        if not ok:
            st.invalid_books += 1
            self.invalid_book_count += 1
            if why == "crossed":
                st.crossed_books += 1
                self.crossed_book_count += 1
            self._warn_once(f"invalid_book:{symbol}:{why}",
                            "Book rejected (%s) for %s — invalid=%d crossed=%d",
                            why, symbol, st.invalid_books, st.crossed_books)
            return

        book = OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ex_ts)
        self._books[symbol] = book

        # Stats
        st.book_updates += 1
        st.last_book_ts = ex_ts
        st.last_book_recv_ts = recv_ts
        st.latency_samples.append(latency_ms)
        st.book_update_history.append(recv_ts)
        st.current_mid = book.mid
        sb = book.spread_bps
        st.current_spread_bps = sb
        if sb is not None and np.isfinite(sb):
            st.spread_samples.append(sb)
        self.book_updates_count += 1

        # Track minute returns for HAR-RV
        mid = book.mid
        if mid:
            self._track_minute_return(symbol, mid, ex_ts)

        # Enqueue — count drops loudly
        try:
            self._book_q.put_nowait(BookUpdate(symbol=symbol, book=book, timestamp=ex_ts))
        except asyncio.QueueFull:
            self.dropped_book_updates_count += 1
            self._warn_once("book_q_full",
                            "book queue FULL — dropped %d book updates",
                            self.dropped_book_updates_count)

    # ------------------------------------------------------------------
    # Trades parser  (does NOT drop a trade because the book isn't ready)
    # ------------------------------------------------------------------

    def _on_trades(self, data) -> None:
        trades_list = data if isinstance(data, list) else [data]
        for td in trades_list:
            symbol = td.get("coin", "").upper()
            if symbol not in self.symbols:
                continue
            st = self._stats[symbol]

            try:
                price  = float(td["px"])
                size   = float(td["sz"])
                side   = td.get("side", "B")
                ex_ts  = float(td.get("time", time.time() * 1000)) / 1000.0
                vol    = price * size
            except (KeyError, ValueError, TypeError):
                continue
            if price <= 0 or size <= 0:
                continue

            recv_ts = time.time()
            latency_ms = max(0.0, (recv_ts - ex_ts) * 1000.0)

            book = self._books.get(symbol)
            best_bid = book.best_bid if book else None
            best_ask = book.best_ask if book else None

            # ALWAYS feed the trade buffer — even if the book isn't ready
            # yet. Downstream consumers can decide what to do with a None
            # bid/ask reference.
            self._trades[symbol].add(
                Trade(timestamp=ex_ts, price=price, size=size,
                      side=side, volume_usd=vol)
            )

            st.trade_events += 1
            st.last_trade_ts = ex_ts
            st.last_trade_recv_ts = recv_ts
            st.trade_history.append(recv_ts)
            st.latency_samples.append(latency_ms)
            self.trade_events_count += 1

            event = TradeEvent(
                symbol=symbol, price=price, size=size, volume_usd=vol,
                side=side, best_bid=best_bid, best_ask=best_ask,
                timestamp=ex_ts, recv_ts=recv_ts, latency_ms=latency_ms,
            )
            try:
                self._trade_q.put_nowait(event)
            except asyncio.QueueFull:
                self.dropped_trade_events_count += 1
                self._warn_once("trade_q_full",
                                "trade queue FULL — dropped %d trade events",
                                self.dropped_trade_events_count)

    # ------------------------------------------------------------------
    # Validators / helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_levels(bids: list, asks: list) -> tuple[bool, str]:
        if not bids and not asks:
            return False, "empty"
        # Positive prices/sizes
        for px, sz in bids:
            if px <= 0 or sz <= 0:
                return False, "bad_bid_value"
        for px, sz in asks:
            if px <= 0 or sz <= 0:
                return False, "bad_ask_value"
        # Sorting
        if any(bids[i][0] < bids[i + 1][0] for i in range(len(bids) - 1)):
            return False, "bids_not_sorted"
        if any(asks[i][0] > asks[i + 1][0] for i in range(len(asks) - 1)):
            return False, "asks_not_sorted"
        # Crossed
        if bids and asks and bids[0][0] >= asks[0][0]:
            return False, "crossed"
        return True, ""

    def _warn_once(self, key: str, msg: str, *args) -> None:
        now = time.time()
        if now - self._last_warn_ts[key] >= _WARN_INTERVAL_S:
            self._last_warn_ts[key] = now
            log.warning(msg, *args)

    # ------------------------------------------------------------------
    # Minute-return tracking
    # ------------------------------------------------------------------

    def _track_minute_return(self, symbol: str, mid: float, ts: float) -> None:
        if symbol not in self._min_open:
            self._min_open[symbol] = mid
            self._min_start_ts[symbol] = ts
            return

        elapsed = ts - self._min_start_ts[symbol]
        if elapsed >= 60.0:
            open_p = self._min_open[symbol]
            if open_p > 0:
                ret = float(np.log(mid / open_p))
                self._minute_returns[symbol] = ret
            self._min_open[symbol] = mid
            self._min_start_ts[symbol] = ts
