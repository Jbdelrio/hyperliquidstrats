"""
orderbook_manager.py — Async WebSocket L2 orderbook + trade stream for Hyperliquid.

Subscribes to:
  • l2Book   per symbol  → maintains OrderBook, feeds book_queue
  • trades   per symbol  → maintains TradesBuffer, feeds trade_queue

Rate-limiting: subscription_delay_s between each subscribe call.
Reconnect: exponential back-off 1s → 64s.
"""
import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

import numpy as np
import websockets

from data.trades_buffer import Trade, TradesBuffer

log = logging.getLogger(__name__)

HL_WS_URL = "wss://api.hyperliquid.xyz/ws"


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class OrderBook:
    symbol: str
    bids: list[tuple[float, float]]   # [(price, size), ...] best-first (descending)
    asks: list[tuple[float, float]]   # [(price, size), ...] best-first (ascending)
    timestamp: float

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
    side: str          # "B" or "A"
    best_bid: float
    best_ask: float
    timestamp: float


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
                 ws_url: str = HL_WS_URL):
        self.symbols = [s.upper() for s in symbols]
        self.sub_delay = subscription_delay_s
        self.ws_url = ws_url

        self._books: dict[str, OrderBook] = {}
        self._trades: dict[str, TradesBuffer] = {s: TradesBuffer() for s in self.symbols}

        # Minute-return tracking (for HAR-RV)
        self._min_open: dict[str, float] = {}
        self._min_start_ts: dict[str, float] = {}
        self._minute_returns: dict[str, Optional[float]] = {}

        # Async queues
        self._book_q: asyncio.Queue = asyncio.Queue(maxsize=50_000)
        self._trade_q: asyncio.Queue = asyncio.Queue(maxsize=50_000)

        self.reconnections: int = 0
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

        levels = data.get("levels", [[], []])
        ts_ms  = data.get("time", time.time() * 1000)
        ts     = ts_ms / 1000.0

        bids = [(float(l["px"]), float(l["sz"])) for l in levels[0]]
        asks = [(float(l["px"]), float(l["sz"])) for l in levels[1]]

        book = OrderBook(symbol=symbol, bids=bids, asks=asks, timestamp=ts)
        self._books[symbol] = book

        # Track minute returns for HAR-RV
        mid = book.mid
        if mid:
            self._track_minute_return(symbol, mid, ts)

        try:
            self._book_q.put_nowait(BookUpdate(symbol=symbol, book=book, timestamp=ts))
        except asyncio.QueueFull:
            pass  # drop — engine is too slow, not a safety issue here

    # ------------------------------------------------------------------
    # Trades parser
    # ------------------------------------------------------------------

    def _on_trades(self, data) -> None:
        trades_list = data if isinstance(data, list) else [data]
        for td in trades_list:
            symbol = td.get("coin", "").upper()
            if symbol not in self.symbols:
                continue

            book = self._books.get(symbol)
            if not book or not book.best_bid or not book.best_ask:
                continue

            try:
                price  = float(td["px"])
                size   = float(td["sz"])
                side   = td.get("side", "B")   # "B"=taker buy, "A"=taker sell
                ts     = float(td.get("time", time.time() * 1000)) / 1000.0
                vol    = price * size
            except (KeyError, ValueError):
                continue

            self._trades[symbol].add(
                Trade(timestamp=ts, price=price, size=size, side=side, volume_usd=vol)
            )

            event = TradeEvent(
                symbol=symbol, price=price, size=size, volume_usd=vol,
                side=side, best_bid=book.best_bid, best_ask=book.best_ask,
                timestamp=ts,
            )
            try:
                self._trade_q.put_nowait(event)
            except asyncio.QueueFull:
                pass

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
