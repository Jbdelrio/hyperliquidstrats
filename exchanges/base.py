"""
exchanges/base.py — Abstract interface for all exchange adapters.

Rules:
  - Data methods (ticker, orderbook, OHLCV, funding) never require live_trading.
  - Order methods check is_live_trading_enabled() before proceeding.
  - Adapters handle network errors gracefully (return None / empty).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd

from exchanges.schemas import (
    ExchangeOrderbook,
    ExchangeTicker,
    OrderRequest,
    OrderResponse,
)


class LiveTradingDisabledError(Exception):
    """Raised when place_order is called but live trading is disabled."""


class ExchangeNotConnectedError(Exception):
    """Raised when a method is called before connect()."""


class BaseExchangeAdapter(ABC):
    name: str = "base"

    # ── Lifecycle ──────────────────────────────────────────────────────────

    @abstractmethod
    def connect(self) -> None:
        """Initialize connection / verify credentials."""

    # ── Market data (never requires live_trading) ──────────────────────────

    @abstractmethod
    def get_markets(self) -> list[dict]:
        """Return list of available markets."""

    @abstractmethod
    def get_ticker(self, symbol: str) -> Optional[ExchangeTicker]:
        """Return current ticker for symbol, or None on error."""

    @abstractmethod
    def get_orderbook(self, symbol: str, depth: int = 20) -> Optional[ExchangeOrderbook]:
        """Return L2 orderbook snapshot, or None on error."""

    @abstractmethod
    def get_ohlcv(self, symbol: str, timeframe: str = "1m",
                  limit: int = 200) -> Optional[pd.DataFrame]:
        """Return OHLCV DataFrame with columns [ts, open, high, low, close, volume]."""

    @abstractmethod
    def get_funding_rate(self, symbol: str) -> Optional[dict]:
        """Return current funding rate dict, or None if unsupported."""

    @abstractmethod
    def get_open_interest(self, symbol: str) -> Optional[dict]:
        """Return open interest dict, or None if unsupported."""

    # ── Account data (requires API key) ───────────────────────────────────

    @abstractmethod
    def get_balance(self) -> dict:
        """Return account balance dict."""

    @abstractmethod
    def get_positions(self) -> list[dict]:
        """Return open positions list."""

    # ── Order management (requires live_trading=True) ──────────────────────

    @abstractmethod
    def place_order(self, order_request: OrderRequest) -> OrderResponse:
        """
        Place an order. Raises LiveTradingDisabledError if live trading off.
        Returns OrderResponse with status='blocked_live_disabled' if blocked.
        """

    @abstractmethod
    def cancel_order(self, order_id: str, symbol: str) -> dict:
        """Cancel an order by ID."""

    # ── Utilities ──────────────────────────────────────────────────────────

    @abstractmethod
    def normalize_symbol(self, symbol: str) -> str:
        """Convert any symbol format to this exchange's native format."""

    @abstractmethod
    def get_fees(self, symbol: str) -> dict:
        """Return {'maker': float, 'taker': float} fee rates."""

    @abstractmethod
    def is_live_trading_enabled(self) -> bool:
        """Return True only if both global and per-exchange live trading flags are set."""

    def _block_if_live_disabled(self, symbol: str) -> OrderResponse:
        return OrderResponse(
            exchange=self.name,
            symbol=symbol,
            status="blocked_live_disabled",
            raw={"reason": f"{self.name} live trading is disabled"},
        )
