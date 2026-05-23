"""Async multi-exchange funding-rate fetcher with per-exchange rate limiting."""
from __future__ import annotations

import asyncio
from typing import Dict, Iterable, List, Optional

import aiohttp

from cli import cache
from config.endpoints import (
    BULK_EXCHANGES, DETAIL_EXCHANGES, EXCHANGE_DELAYS,
    EXCHANGES, HISTORY_EXCHANGES,
)
from config.settings import RATE_LIMIT_DELAY
from utils.logger import get_logger

log = get_logger(__name__)


class ExchangeRateLimiter:
    """Per-exchange lock + min-delay between calls — protects against bans."""

    def __init__(self, default_delay: float = RATE_LIMIT_DELAY):
        self.default_delay = default_delay
        self._locks: Dict[str, asyncio.Lock] = {}
        self._last_call: Dict[str, float] = {}

    def _lock(self, exchange: str) -> asyncio.Lock:
        if exchange not in self._locks:
            self._locks[exchange] = asyncio.Lock()
        return self._locks[exchange]

    def _delay(self, exchange: str) -> float:
        return EXCHANGE_DELAYS.get(exchange, self.default_delay)

    async def wait(self, exchange: str) -> None:
        async with self._lock(exchange):
            loop = asyncio.get_event_loop()
            last = self._last_call.get(exchange, 0.0)
            elapsed = loop.time() - last
            delay = self._delay(exchange)
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_call[exchange] = loop.time()


async def _fetch_one(
    session: aiohttp.ClientSession,
    limiter: ExchangeRateLimiter,
    exchange: str,
    coin: str,
    use_cache: bool,
) -> Optional[Dict]:
    if use_cache:
        cached = cache.get(exchange, coin)
        if cached is not None:
            log.debug("cache hit %s/%s", exchange, coin)
            return cached

    adapter = EXCHANGES.get(exchange)
    if adapter is None:
        log.warning("unknown exchange: %s", exchange)
        return None

    await limiter.wait(exchange)
    try:
        result = await adapter(session, coin)
    except asyncio.TimeoutError:
        log.warning("timeout %s/%s", exchange, coin)
        return None
    except aiohttp.ClientError as e:
        log.warning("client error %s/%s: %s", exchange, coin, e)
        return None
    except Exception as e:
        log.warning("fetch failed %s/%s: %s", exchange, coin, e)
        return None

    if result is not None and use_cache:
        cache.put(exchange, coin, result)
    return result


async def fetch_many(
    coins: Iterable[str],
    exchanges: Iterable[str],
    use_cache: bool = True,
) -> List[Dict]:
    """Fan out across all (exchange, coin) pairs; returns only successful rows."""
    limiter = ExchangeRateLimiter()
    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [
            _fetch_one(session, limiter, ex, coin, use_cache)
            for coin in coins
            for ex in exchanges
        ]
        results = await asyncio.gather(*tasks, return_exceptions=False)
    return [r for r in results if r is not None]


def fetch_many_sync(
    coins: Iterable[str],
    exchanges: Iterable[str],
    use_cache: bool = True,
) -> List[Dict]:
    """Sync wrapper for Dash callbacks (which run on threads, not the event loop)."""
    return _run_async(fetch_many(coins, exchanges, use_cache))


# ---------------------------------------------------------------------------
# Top-N: list ALL perps for a single exchange, sort by funding-rate.
# ---------------------------------------------------------------------------

_TOP_CACHE_KEY = "__topN__"  # synthetic cache "coin" for bulk listings


async def fetch_top_n(
    exchange: str,
    n: int = 10,
    mode: str = "abs",
    use_cache: bool = True,
) -> List[Dict]:
    """Return the top-N perps for `exchange` sorted by rate (mode: abs/high/low)."""
    if exchange not in BULK_EXCHANGES:
        log.warning("top-N not supported for %s (no bulk endpoint)", exchange)
        return []

    cached = cache.get(exchange, _TOP_CACHE_KEY) if use_cache else None
    if cached is not None and isinstance(cached, list):
        rows = cached
    else:
        connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
        async with aiohttp.ClientSession(connector=connector) as session:
            try:
                rows = await BULK_EXCHANGES[exchange](session)
            except Exception as e:
                log.exception("top-N fetch failed for %s: %s", exchange, e)
                return []
        if use_cache and rows:
            # Cache the whole list under one key; payload is a JSON-able list.
            cache.put(exchange, _TOP_CACHE_KEY, rows)

    if mode == "high":
        rows = sorted(rows, key=lambda r: r["rate"], reverse=True)
    elif mode == "low":
        rows = sorted(rows, key=lambda r: r["rate"])
    else:  # abs
        rows = sorted(rows, key=lambda r: abs(r["rate"]), reverse=True)
    return rows[:n]


def fetch_top_n_sync(exchange: str, n: int = 10, mode: str = "abs",
                     use_cache: bool = True) -> List[Dict]:
    return _run_async(fetch_top_n(exchange, n, mode, use_cache))


def _run_async(coro):
    """Run a coroutine even when a loop is already running (Dash callbacks)."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                return pool.submit(lambda: asyncio.run(coro)).result()
    except RuntimeError:
        pass
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# History + detail wrappers
# ---------------------------------------------------------------------------

async def fetch_history(exchange: str, symbol: str, limit: int = 50) -> List[Dict]:
    if exchange not in HISTORY_EXCHANGES:
        log.warning("history not supported for %s", exchange)
        return []
    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            return await HISTORY_EXCHANGES[exchange](session, symbol, limit)
        except Exception as e:
            log.exception("history failed for %s/%s: %s", exchange, symbol, e)
            return []


async def fetch_detail(exchange: str, symbol: str) -> Optional[Dict]:
    if exchange not in DETAIL_EXCHANGES:
        log.warning("detail not supported for %s", exchange)
        return None
    connector = aiohttp.TCPConnector(limit=20, ttl_dns_cache=300)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            return await DETAIL_EXCHANGES[exchange](session, symbol)
        except Exception as e:
            log.exception("detail failed for %s/%s: %s", exchange, symbol, e)
            return None


async def fetch_all(exchange: str, use_cache: bool = True) -> List[Dict]:
    """Return ALL perpetuals for one exchange — alias for fetch_top_n with no cap."""
    return await fetch_top_n(exchange, n=10**9, mode="abs", use_cache=use_cache)


def fetch_history_sync(exchange, symbol, limit=50):
    return _run_async(fetch_history(exchange, symbol, limit))


def fetch_detail_sync(exchange, symbol):
    return _run_async(fetch_detail(exchange, symbol))


def fetch_all_sync(exchange, use_cache=True):
    return _run_async(fetch_all(exchange, use_cache))
