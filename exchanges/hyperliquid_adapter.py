"""
exchanges/hyperliquid_adapter.py — Wrapper around existing Hyperliquid infrastructure.

Exposes the same BaseExchangeAdapter interface without breaking the existing
OrderbookManager or executor. This adapter is data-read-only for cross-exchange
comparison; actual execution still goes through the existing high_freq_executor.
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

_ENABLED      = os.environ.get("HYPERLIQUID_ENABLED", "true").lower() in ("1", "true", "yes")
_LIVE_TRADING = os.environ.get("HYPERLIQUID_LIVE_TRADING", "false").lower() in ("1", "true", "yes")
_WS_URL       = os.environ.get("HYPERLIQUID_WS_URL", "wss://api.hyperliquid.xyz/ws")
_REST_URL     = os.environ.get("HYPERLIQUID_REST_URL", "https://api.hyperliquid.xyz/info")


class HyperliquidAdapter(BaseExchangeAdapter):
    """
    Thin adapter around the existing OrderbookManager.
    Provides data-read interface for the exchange comparison layer.
    Execution is NOT handled here — use high_freq_executor.py for that.
    """
    name = "hyperliquid"

    def __init__(self, orderbook_manager=None) -> None:
        self._obm = orderbook_manager  # injected from engine (can be None)

    def connect(self) -> None:
        log.info("HyperliquidAdapter: using existing OrderbookManager connection")

    def get_markets(self) -> list[dict]:
        if self._obm:
            return [{"symbol": s} for s in getattr(self._obm, "symbols", [])]
        return []

    def get_ticker(self, symbol: str) -> Optional[ExchangeTicker]:
        sym = self.normalize_symbol(symbol)
        if self._obm is None:
            return None
        try:
            book = self._obm.get_book(sym)
            if book is None:
                return None
            ts = datetime.now(timezone.utc).isoformat()
            return ExchangeTicker(
                exchange="hyperliquid",
                symbol=sym,
                timestamp=ts,
                bid=getattr(book, "best_bid", None),
                ask=getattr(book, "best_ask", None),
            )
        except Exception as exc:
            log.debug("HyperliquidAdapter.get_ticker error: %s", exc)
            return None

    def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[ExchangeOrderbook]:
        sym = self.normalize_symbol(symbol)
        if self._obm is None:
            return None
        try:
            book = self._obm.get_book(sym)
            if book is None:
                return None
            ts = datetime.now(timezone.utc).isoformat()
            bids = getattr(book, "bids", []) or []
            asks = getattr(book, "asks", []) or []
            return ExchangeOrderbook(
                exchange="hyperliquid",
                symbol=sym,
                timestamp=ts,
                bids=[[float(p), float(s)] for p, s in bids[:depth]],
                asks=[[float(p), float(s)] for p, s in asks[:depth]],
                depth=depth,
            )
        except Exception as exc:
            log.debug("HyperliquidAdapter.get_orderbook error: %s", exc)
            return None

    def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                  limit: int = 200) -> Optional[pd.DataFrame]:
        """
        Hyperliquid doesn't have a direct OHLCV REST endpoint in the existing stack.
        Returns None — use bars_history from engine instead.
        """
        return None

    def get_funding_rate(self, symbol: str) -> Optional[dict]:
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.post(
                _REST_URL,
                json={"type": "metaAndAssetCtxs"},
                timeout=5,
            )
            data = resp.json()
            assets = data[0].get("universe", []) if isinstance(data, list) else []
            ctx    = data[1] if len(data) > 1 else []
            for i, asset in enumerate(assets):
                if asset.get("name", "").upper() == sym.upper():
                    c = ctx[i] if i < len(ctx) else {}
                    return {
                        "symbol":       sym,
                        "funding_rate": float(c.get("funding", 0)),
                        "timestamp":    datetime.now(timezone.utc).isoformat(),
                    }
        except Exception as exc:
            log.debug("HyperliquidAdapter.get_funding_rate error: %s", exc)
        return None

    def get_open_interest(self, symbol: str) -> Optional[dict]:
        sym = self.normalize_symbol(symbol)
        try:
            import requests
            resp = requests.post(
                _REST_URL,
                json={"type": "metaAndAssetCtxs"},
                timeout=5,
            )
            data = resp.json()
            assets = data[0].get("universe", []) if isinstance(data, list) else []
            ctx    = data[1] if len(data) > 1 else []
            for i, asset in enumerate(assets):
                if asset.get("name", "").upper() == sym.upper():
                    c = ctx[i] if i < len(ctx) else {}
                    return {
                        "symbol": sym,
                        "open_interest": float(c.get("openInterest", 0)),
                    }
        except Exception as exc:
            log.debug("HyperliquidAdapter.get_open_interest error: %s", exc)
        return None

    def get_balance(self) -> dict:
        return {"note": "Use high_freq_executor for balance in paper mode"}

    def get_positions(self) -> list[dict]:
        return []

    def place_order(self, order_request: OrderRequest) -> OrderResponse:
        if not self.is_live_trading_enabled():
            return self._block_if_live_disabled(order_request.symbol)
        raise NotImplementedError(
            "Live execution via HyperliquidAdapter is not implemented. "
            "Use high_freq_executor.py for execution."
        )

    def cancel_order(self, order_id: str, symbol: str) -> dict:
        return {"status": "not_implemented"}

    def normalize_symbol(self, symbol: str) -> str:
        return symbol.upper().replace("-USD", "").replace("/USDT", "").replace("USDT", "")

    def get_fees(self, symbol: str) -> dict:
        return {"maker": -0.0003, "taker": 0.0003}

    def is_live_trading_enabled(self) -> bool:
        global_live = os.environ.get("GLOBAL_LIVE_TRADING", "false").lower() in ("1", "true", "yes")
        return global_live and _LIVE_TRADING
