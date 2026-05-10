"""
exchanges/bitget_adapter.py — Bitget USDT-M perpetual futures adapter.

Data-only by default. Live trading requires explicit env vars + passphrase.
Uses requests only — no ccxt required.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from base64 import b64encode
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from exchanges.base import BaseExchangeAdapter
from exchanges.schemas import (
    ExchangeOrderbook,
    ExchangeTicker,
    OrderRequest,
    OrderResponse,
)

log = logging.getLogger(__name__)

_ENABLED      = os.environ.get("BITGET_ENABLED", "false").lower() in ("1", "true", "yes")
_API_KEY      = os.environ.get("BITGET_API_KEY", "")
_API_SECRET   = os.environ.get("BITGET_API_SECRET", "")
_PASSPHRASE   = os.environ.get("BITGET_API_PASSPHRASE", "")
_TESTNET      = os.environ.get("BITGET_TESTNET", "true").lower() in ("1", "true", "yes")
_LIVE_TRADING = os.environ.get("BITGET_LIVE_TRADING", "false").lower() in ("1", "true", "yes")

_BASE_URL = "https://api.bitget.com"   # Bitget has no separate testnet URL for futures


class BitgetAdapter(BaseExchangeAdapter):
    name = "bitget"

    def __init__(self) -> None:
        self._connected = False

    def connect(self) -> None:
        if not _ENABLED:
            log.info("BitgetAdapter: disabled (BITGET_ENABLED=false)")
            return
        try:
            import requests
            resp = requests.get(f"{_BASE_URL}/api/v2/public/time", timeout=5)
            resp.raise_for_status()
            self._connected = True
            log.info("BitgetAdapter: connected")
        except Exception as exc:
            log.warning("BitgetAdapter: connect failed: %s", exc)

    def get_markets(self) -> list[dict]:
        if not _ENABLED:
            return []
        try:
            import requests
            resp = requests.get(
                f"{_BASE_URL}/api/v2/mix/market/contracts",
                params={"productType": "USDT-FUTURES"},
                timeout=10,
            )
            data = resp.json()
            symbols = data.get("data", [])
            return [{"symbol": s.get("symbol"), "status": s.get("symbolStatus")}
                    for s in symbols]
        except Exception as exc:
            log.debug("BitgetAdapter.get_markets error: %s", exc)
            return []

    def get_ticker(self, symbol: str) -> Optional[ExchangeTicker]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.get(
                f"{_BASE_URL}/api/v2/mix/market/ticker",
                params={"symbol": sym, "productType": "USDT-FUTURES"},
                timeout=5,
            )
            data = resp.json()
            d = (data.get("data") or [{}])[0] if isinstance(data.get("data"), list) else {}
            if not d:
                return None
            ts = datetime.now(timezone.utc).isoformat()
            bid = float(d.get("bidPr", 0)) or None
            ask = float(d.get("askPr", 0)) or None
            return ExchangeTicker(
                exchange="bitget",
                symbol=sym,
                timestamp=ts,
                bid=bid,
                ask=ask,
                last=float(d.get("lastPr", 0)) or None,
                volume_24h=float(d.get("quoteVolume", 0)) or None,
            )
        except Exception as exc:
            log.debug("BitgetAdapter.get_ticker error: %s", exc)
            return None

    def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[ExchangeOrderbook]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.get(
                f"{_BASE_URL}/api/v2/mix/market/merge-depth",
                params={"symbol": sym, "productType": "USDT-FUTURES",
                        "limit": str(min(depth, 100))},
                timeout=5,
            )
            data = resp.json().get("data", {})
            ts = datetime.now(timezone.utc).isoformat()
            bids = [[float(b[0]), float(b[1])] for b in data.get("bids", [])[:depth]]
            asks = [[float(a[0]), float(a[1])] for a in data.get("asks", [])[:depth]]
            return ExchangeOrderbook(
                exchange="bitget",
                symbol=sym,
                timestamp=ts,
                bids=bids,
                asks=asks,
                depth=depth,
            )
        except Exception as exc:
            log.debug("BitgetAdapter.get_orderbook error: %s", exc)
            return None

    def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                  limit: int = 200) -> Optional[pd.DataFrame]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        gran_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1H", "4h": "4H", "1d": "1D"}
        gran = gran_map.get(timeframe, "1m")
        try:
            import requests
            resp = requests.get(
                f"{_BASE_URL}/api/v2/mix/market/candles",
                params={
                    "symbol": sym, "productType": "USDT-FUTURES",
                    "granularity": gran, "limit": str(limit),
                },
                timeout=10,
            )
            data = resp.json().get("data", [])
            if not data:
                return None
            rows = []
            for k in data:
                rows.append({
                    "ts":     int(k[0]) / 1000,
                    "open":   float(k[1]),
                    "high":   float(k[2]),
                    "low":    float(k[3]),
                    "close":  float(k[4]),
                    "volume": float(k[5]),
                })
            return pd.DataFrame(rows)
        except Exception as exc:
            log.debug("BitgetAdapter.get_ohlcv error: %s", exc)
            return None

    def get_funding_rate(self, symbol: str) -> Optional[dict]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.get(
                f"{_BASE_URL}/api/v2/mix/market/current-fund-rate",
                params={"symbol": sym, "productType": "USDT-FUTURES"},
                timeout=5,
            )
            data = resp.json().get("data", {})
            return {
                "symbol":       sym,
                "funding_rate": float(data.get("fundingRate", 0)),
                "timestamp":    datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            log.debug("BitgetAdapter.get_funding_rate error: %s", exc)
            return None

    def get_open_interest(self, symbol: str) -> Optional[dict]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.get(
                f"{_BASE_URL}/api/v2/mix/market/open-interest",
                params={"symbol": sym, "productType": "USDT-FUTURES"},
                timeout=5,
            )
            data = resp.json().get("data", {})
            return {
                "symbol":        sym,
                "open_interest": float(data.get("size", 0)),
            }
        except Exception as exc:
            log.debug("BitgetAdapter.get_open_interest error: %s", exc)
            return None

    def get_balance(self) -> dict:
        return {"note": "account balance requires signed request"}

    def get_positions(self) -> list[dict]:
        return []

    def place_order(self, order_request: OrderRequest) -> OrderResponse:
        if not self.is_live_trading_enabled():
            return self._block_if_live_disabled(order_request.symbol)
        raise NotImplementedError("Bitget live order placement not yet implemented")

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        return {"status": "not_implemented"}

    def normalize_symbol(self, symbol: str) -> str:
        s = symbol.upper()
        s = s.replace("-USDT", "USDT").replace("/USDT", "USDT").replace("-USD", "USDT")
        if not s.endswith("USDT"):
            s += "USDT"
        return s

    def get_fees(self, symbol: str) -> dict:
        return {"maker": 0.0002, "taker": 0.0006}

    def is_live_trading_enabled(self) -> bool:
        global_live = os.environ.get("GLOBAL_LIVE_TRADING", "false").lower() in ("1", "true", "yes")
        return _ENABLED and global_live and _LIVE_TRADING
