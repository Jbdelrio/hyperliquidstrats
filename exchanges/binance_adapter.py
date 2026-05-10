"""
exchanges/binance_adapter.py — Binance USDT-M Futures adapter (data + optional live).

Data-only by default. Live trading requires explicit env vars.
Uses requests only — no ccxt or binance-python required.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from exchanges.base import BaseExchangeAdapter, LiveTradingDisabledError
from exchanges.schemas import (
    ExchangeOrderbook,
    ExchangeTicker,
    OrderRequest,
    OrderResponse,
)

log = logging.getLogger(__name__)

_ENABLED       = os.environ.get("BINANCE_ENABLED", "false").lower() in ("1", "true", "yes")
_API_KEY       = os.environ.get("BINANCE_API_KEY", "")
_API_SECRET    = os.environ.get("BINANCE_API_SECRET", "")
_TESTNET       = os.environ.get("BINANCE_TESTNET", "true").lower() in ("1", "true", "yes")
_LIVE_TRADING  = os.environ.get("BINANCE_LIVE_TRADING", "false").lower() in ("1", "true", "yes")

_BASE_URL_LIVE    = "https://fapi.binance.com"
_BASE_URL_TESTNET = "https://testnet.binancefuture.com"


def _base_url() -> str:
    return _BASE_URL_TESTNET if _TESTNET else _BASE_URL_LIVE


class BinanceAdapter(BaseExchangeAdapter):
    name = "binance"

    def __init__(self) -> None:
        self._connected = False

    def connect(self) -> None:
        if not _ENABLED:
            log.info("BinanceAdapter: disabled (BINANCE_ENABLED=false)")
            return
        try:
            import requests
            resp = requests.get(f"{_base_url()}/fapi/v1/ping", timeout=5)
            resp.raise_for_status()
            self._connected = True
            log.info("BinanceAdapter: connected (testnet=%s)", _TESTNET)
        except Exception as exc:
            log.warning("BinanceAdapter: connect failed: %s", exc)

    def get_markets(self) -> list[dict]:
        if not _ENABLED:
            return []
        try:
            import requests
            resp = requests.get(f"{_base_url()}/fapi/v1/exchangeInfo", timeout=10)
            data = resp.json()
            return [{"symbol": s["symbol"], "status": s["status"]}
                    for s in data.get("symbols", [])]
        except Exception as exc:
            log.debug("BinanceAdapter.get_markets error: %s", exc)
            return []

    def get_ticker(self, symbol: str) -> Optional[ExchangeTicker]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.get(
                f"{_base_url()}/fapi/v1/ticker/bookTicker",
                params={"symbol": sym},
                timeout=5,
            )
            data = resp.json()
            if "code" in data:
                return None
            ts = datetime.now(timezone.utc).isoformat()
            bid = float(data["bidPrice"])
            ask = float(data["askPrice"])
            return ExchangeTicker(
                exchange="binance",
                symbol=sym,
                timestamp=ts,
                bid=bid,
                ask=ask,
            )
        except Exception as exc:
            log.debug("BinanceAdapter.get_ticker error: %s", exc)
            return None

    def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[ExchangeOrderbook]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        limit = min(depth, 1000)
        try:
            import requests
            resp = requests.get(
                f"{_base_url()}/fapi/v1/depth",
                params={"symbol": sym, "limit": limit},
                timeout=5,
            )
            data = resp.json()
            if "code" in data:
                return None
            ts = datetime.now(timezone.utc).isoformat()
            bids = [[float(b[0]), float(b[1])] for b in data.get("bids", [])[:depth]]
            asks = [[float(a[0]), float(a[1])] for a in data.get("asks", [])[:depth]]
            return ExchangeOrderbook(
                exchange="binance",
                symbol=sym,
                timestamp=ts,
                bids=bids,
                asks=asks,
                depth=depth,
            )
        except Exception as exc:
            log.debug("BinanceAdapter.get_orderbook error: %s", exc)
            return None

    def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                  limit: int = 200) -> Optional[pd.DataFrame]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        interval_map = {"1m": "1m", "5m": "5m", "15m": "15m", "1h": "1h", "4h": "4h", "1d": "1d"}
        interval = interval_map.get(timeframe, "1m")
        try:
            import requests
            resp = requests.get(
                f"{_base_url()}/fapi/v1/klines",
                params={"symbol": sym, "interval": interval, "limit": limit},
                timeout=10,
            )
            data = resp.json()
            if not data or isinstance(data, dict):
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
            log.debug("BinanceAdapter.get_ohlcv error: %s", exc)
            return None

    def get_funding_rate(self, symbol: str) -> Optional[dict]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.get(
                f"{_base_url()}/fapi/v1/premiumIndex",
                params={"symbol": sym},
                timeout=5,
            )
            data = resp.json()
            if "code" in data:
                return None
            return {
                "symbol":       sym,
                "funding_rate": float(data.get("lastFundingRate", 0)),
                "timestamp":    datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            log.debug("BinanceAdapter.get_funding_rate error: %s", exc)
            return None

    def get_open_interest(self, symbol: str) -> Optional[dict]:
        if not _ENABLED:
            return None
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.get(
                f"{_base_url()}/fapi/v1/openInterest",
                params={"symbol": sym},
                timeout=5,
            )
            data = resp.json()
            if "code" in data:
                return None
            return {
                "symbol":        sym,
                "open_interest": float(data.get("openInterest", 0)),
            }
        except Exception as exc:
            log.debug("BinanceAdapter.get_open_interest error: %s", exc)
            return None

    def get_balance(self) -> dict:
        if not _API_KEY:
            return {"error": "no API key"}
        return {"note": "account balance requires signed request"}

    def get_positions(self) -> list[dict]:
        return []

    def place_order(self, order_request: OrderRequest) -> OrderResponse:
        if not self.is_live_trading_enabled():
            return self._block_if_live_disabled(order_request.symbol)
        raise NotImplementedError("Binance live order placement not yet implemented")

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        return {"status": "not_implemented"}

    def normalize_symbol(self, symbol: str) -> str:
        s = symbol.upper()
        # Order matters: handle -USDT before -USD to avoid partial replacement
        s = s.replace("-USDT", "USDT").replace("/USDT", "USDT").replace("-USD", "USDT")
        if not s.endswith("USDT"):
            s += "USDT"
        return s

    def get_fees(self, symbol: str) -> dict:
        return {"maker": 0.0002, "taker": 0.0005}

    def is_live_trading_enabled(self) -> bool:
        global_live = os.environ.get("GLOBAL_LIVE_TRADING", "false").lower() in ("1", "true", "yes")
        return _ENABLED and global_live and _LIVE_TRADING
