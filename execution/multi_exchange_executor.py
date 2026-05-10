"""
execution/multi_exchange_executor.py — Normalized multi-exchange order dispatcher.

Receives an OrderRequest, routes it through the correct adapter, enforces
all live-trading safety checks. The LLM NEVER calls this directly.

Safety invariants (enforced here):
  - GLOBAL_LIVE_TRADING must be true
  - The specific exchange's LIVE_TRADING must be true
  - Unknown exchange → OrderResponse(status="error")
  - Disabled exchange → OrderResponse(status="blocked_live_disabled")
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from exchanges.factory import get_exchange
from exchanges.schemas import OrderRequest, OrderResponse

log = logging.getLogger(__name__)


class MultiExchangeExecutor:
    """Route normalized OrderRequests to the correct adapter."""

    def place_order(self, request: OrderRequest) -> OrderResponse:
        exchange_name = request.exchange.lower()
        adapter = get_exchange(exchange_name)

        if adapter is None:
            log.error("MultiExchangeExecutor: unknown exchange '%s'", exchange_name)
            return OrderResponse(
                exchange=exchange_name,
                symbol=request.symbol,
                status="error",
                raw={"error": f"unknown exchange: {exchange_name}"},
            )

        if not adapter.is_live_trading_enabled():
            log.warning(
                "MultiExchangeExecutor: live trading disabled for '%s' — blocking order",
                exchange_name,
            )
            return adapter._block_if_live_disabled(request.symbol)

        # Assign client_order_id if not provided
        if not request.client_order_id:
            request.client_order_id = f"art_{uuid.uuid4().hex[:12]}"

        try:
            response = adapter.place_order(request)
            log.info(
                "[MultiExec] %s %s %s %s size=%.4f price=%s status=%s",
                exchange_name, request.symbol, request.side,
                request.order_type, request.size,
                request.price or "market", response.status,
            )
            return response
        except Exception as exc:
            log.error("MultiExchangeExecutor.place_order error: %s", exc, exc_info=True)
            return OrderResponse(
                exchange=exchange_name,
                symbol=request.symbol,
                status="error",
                raw={"error": str(exc)},
            )

    def cancel_order(self, exchange_name: str,
                     order_id: str, symbol: str) -> dict:
        adapter = get_exchange(exchange_name)
        if adapter is None:
            return {"status": "error", "error": f"unknown exchange: {exchange_name}"}
        try:
            return adapter.cancel_order(order_id, symbol)
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
