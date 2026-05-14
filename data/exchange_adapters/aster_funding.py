"""
aster_funding.py — FundingSnapshot adapter for Aster Exchange.

Aster's REST funding-rate endpoint is not exposed/documented in this
repo at the time of writing. The adapter is therefore implemented in
"unavailable" mode by default :
    - `available` is False unless an endpoint URL is configured,
    - `fetch()` returns an empty dict,
    - never crashes the engine.

To wire the real endpoint :
    1. Discover the actual REST path (e.g. /fapi/v1/premiumIndex on a
       Binance-style API). Add it to `Asterfunding.REST_URL`.
    2. Parse the JSON to populate `FundingSnapshot.funding_rate` as an
       HOURLY rate (convert if the API exposes 8h or per-period).
    3. Set `self.available = True`.

DO NOT push trades through this adapter until points 1–3 are done, and
even then keep `allow_live=false` in the config until paper/backtest
validate the carry.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from data.funding_data import FundingSnapshot

log = logging.getLogger(__name__)


class AsterFundingAdapter:
    EXCHANGE = "aster"
    REST_URL: Optional[str] = None  # set when integration is implemented

    def __init__(self, min_refresh_interval_s: float = 60.0):
        self.min_refresh_interval_s = float(min_refresh_interval_s)
        self._available = bool(self.REST_URL)
        self._last_fetch_ts: float = 0.0
        self._cache: dict[str, FundingSnapshot] = {}
        if not self._available:
            log.info("AsterFundingAdapter disabled (no REST_URL configured); "
                     "scanner will log 'aster_funding_unavailable'.")

    @property
    def available(self) -> bool:
        return self._available

    def fetch(self, symbols: Optional[list[str]] = None,
              force: bool = False) -> dict[str, FundingSnapshot]:
        if not self._available:
            return {}
        # TODO : implement real Aster REST call here.  When implementing,
        # convert any 8h / per-period rate to per-hour for consistency
        # with HyperliquidFundingAdapter.
        return {}
