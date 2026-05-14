"""
hyperliquid_funding.py — FundingSnapshot adapter for Hyperliquid.

Wraps the existing `data/hyperliquid_funding.fetch_hyperliquid_funding_rates`
fetcher (REST `metaAndAssetCtxs`) and returns standardized
`FundingSnapshot` objects.

NOTE on funding frequency : Hyperliquid pays funding every hour. The
`raw_8h` field returned by `metaAndAssetCtxs` is already a per-period
rate ; the existing fetcher divides by 8 to get a per-hour value. We
keep that convention (per-hour) so cross-exchange comparison is
apples-to-apples.

Adapter NEVER spams REST : the engine should call `refresh` at most once
per `min_refresh_interval_s` (60–300 s typical).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from data.funding_data import FundingSnapshot
from data.hyperliquid_funding import fetch_hyperliquid_funding_rates

log = logging.getLogger(__name__)


class HyperliquidFundingAdapter:
    EXCHANGE = "hyperliquid"

    def __init__(self, min_refresh_interval_s: float = 60.0):
        self.min_refresh_interval_s = float(min_refresh_interval_s)
        self._last_fetch_ts: float = 0.0
        self._cache: dict[str, FundingSnapshot] = {}

    @property
    def available(self) -> bool:
        return True

    def fetch(self, symbols: Optional[list[str]] = None,
              force: bool = False) -> dict[str, FundingSnapshot]:
        """Return {symbol: FundingSnapshot}. Uses cache when fresh."""
        now = time.time()
        if (not force) and (now - self._last_fetch_ts) < self.min_refresh_interval_s:
            if self._cache:
                if symbols:
                    return {s: self._cache[s] for s in symbols if s in self._cache}
                return dict(self._cache)
        try:
            rates = fetch_hyperliquid_funding_rates(coins=symbols)
        except Exception as e:
            log.warning("Hyperliquid funding fetch failed: %s", e)
            return dict(self._cache) if self._cache else {}
        out: dict[str, FundingSnapshot] = {}
        for coin, info in rates.items():
            try:
                hourly = float(info.get("hourly_rate", 0.0))
            except (TypeError, ValueError):
                continue
            snap = FundingSnapshot(
                exchange=self.EXCHANGE,
                symbol=coin.upper(),
                timestamp=now,
                funding_rate=hourly,
                raw=dict(info),
            )
            out[snap.symbol] = snap
        if out:
            self._cache = out
            self._last_fetch_ts = now
        return out
