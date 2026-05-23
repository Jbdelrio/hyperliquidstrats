"""SQLite-backed cache for funding-rate results (5-min TTL by default)."""
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from typing import Dict, Iterator, Optional

from config.settings import CACHE_DB_PATH, CACHE_TTL_SECONDS


SCHEMA = """
CREATE TABLE IF NOT EXISTS funding_cache (
    exchange     TEXT NOT NULL,
    coin         TEXT NOT NULL,
    payload      TEXT NOT NULL,
    cached_at_ms INTEGER NOT NULL,
    PRIMARY KEY (exchange, coin)
);
CREATE INDEX IF NOT EXISTS idx_funding_cached_at ON funding_cache(cached_at_ms);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(str(CACHE_DB_PATH))
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(SCHEMA)


def get(exchange: str, coin: str, ttl_seconds: int = CACHE_TTL_SECONDS) -> Optional[Dict]:
    cutoff_ms = int(time.time() * 1000) - (ttl_seconds * 1000)
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload, cached_at_ms FROM funding_cache "
            "WHERE exchange = ? AND coin = ? AND cached_at_ms >= ?",
            (exchange, coin, cutoff_ms),
        ).fetchone()
    if row is None:
        return None
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return None


def put(exchange: str, coin: str, payload: Dict) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO funding_cache (exchange, coin, payload, cached_at_ms) "
            "VALUES (?, ?, ?, ?)",
            (exchange, coin, json.dumps(payload), int(time.time() * 1000)),
        )


def clear() -> int:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM funding_cache")
        return cur.rowcount


init_db()
