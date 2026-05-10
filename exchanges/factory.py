"""
exchanges/factory.py — Exchange adapter factory.

Returns adapter instances based on configuration.
Hyperliquid is the default. Binance/Bitget are opt-in.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger(__name__)

_DEFAULT_EXCHANGE  = os.environ.get("DEFAULT_EXCHANGE", "hyperliquid").lower()
_ENABLED_EXCHANGES = [
    e.strip().lower()
    for e in os.environ.get("ENABLED_EXCHANGES", "hyperliquid").split(",")
    if e.strip()
]

# Global registry: name → adapter instance (lazy-initialized)
_REGISTRY: dict[str, object] = {}
_OBM_REF = None   # injected by engine so HyperliquidAdapter can share the connection


def set_orderbook_manager(obm) -> None:
    """Called by engine to inject the existing OrderbookManager into the HL adapter."""
    global _OBM_REF
    _OBM_REF = obm
    if "hyperliquid" in _REGISTRY:
        _REGISTRY["hyperliquid"]._obm = obm


def get_exchange(name: str) -> Optional[object]:
    """Return (and lazily create) adapter for `name`. None if unknown."""
    name = name.lower()
    if name in _REGISTRY:
        return _REGISTRY[name]

    adapter = _create(name)
    if adapter is not None:
        _REGISTRY[name] = adapter
    return adapter


def get_enabled_exchanges() -> list:
    """Return all enabled exchange adapters."""
    adapters = []
    for name in _ENABLED_EXCHANGES:
        a = get_exchange(name)
        if a is not None:
            adapters.append(a)
    return adapters


def get_default_exchange():
    """Return the primary execution exchange (Hyperliquid by default)."""
    return get_exchange(_DEFAULT_EXCHANGE)


def _create(name: str):
    try:
        if name == "hyperliquid":
            from exchanges.hyperliquid_adapter import HyperliquidAdapter
            adapter = HyperliquidAdapter(orderbook_manager=_OBM_REF)
            return adapter

        if name == "binance":
            from exchanges.binance_adapter import BinanceAdapter
            import os as _os
            if _os.environ.get("BINANCE_ENABLED", "false").lower() not in ("1", "true", "yes"):
                log.debug("BinanceAdapter skipped: BINANCE_ENABLED=false")
                return None
            adapter = BinanceAdapter()
            adapter.connect()
            return adapter

        if name == "bitget":
            from exchanges.bitget_adapter import BitgetAdapter
            import os as _os
            if _os.environ.get("BITGET_ENABLED", "false").lower() not in ("1", "true", "yes"):
                log.debug("BitgetAdapter skipped: BITGET_ENABLED=false")
                return None
            adapter = BitgetAdapter()
            adapter.connect()
            return adapter

        log.warning("Unknown exchange: %s", name)
        return None

    except Exception as exc:
        log.warning("Exchange adapter creation failed for %s: %s", name, exc)
        return None


def collect_cross_exchange_data(symbol: str,
                                exclude: str = "hyperliquid") -> Optional[dict]:
    """
    Collect ticker data from all enabled exchanges except `exclude`.
    Returns dict suitable for MarketSnapshot.cross_exchange_data, or None.
    """
    enabled = _ENABLED_EXCHANGES
    if len(enabled) <= 1:
        return None  # only one exchange, no cross-exchange comparison possible

    result = {}
    for name in enabled:
        if name == exclude:
            continue
        adapter = get_exchange(name)
        if adapter is None:
            continue
        try:
            ticker = adapter.get_ticker(symbol)
            funding = adapter.get_funding_rate(symbol)
            ob      = adapter.get_orderbook(symbol, depth=5)
            result[name] = {
                "mid":                ticker.mid if ticker else None,
                "spread_bps":         ticker.spread_bps if ticker else None,
                "funding_rate":       funding.get("funding_rate") if funding else None,
                "orderbook_imbalance": ob.imbalance if ob else None,
            }
        except Exception as exc:
            log.debug("collect_cross_exchange_data %s/%s error: %s", name, symbol, exc)

    return result if result else None
