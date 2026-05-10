"""
exchanges/schemas.py — Normalized data schemas for all exchange adapters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ExchangeTicker:
    exchange: str
    symbol: str
    timestamp: str
    bid: Optional[float] = None
    ask: Optional[float] = None
    mid: Optional[float] = None
    last: Optional[float] = None
    spread_bps: Optional[float] = None
    volume_24h: Optional[float] = None

    def __post_init__(self) -> None:
        if self.bid and self.ask and self.bid > 0 and self.spread_bps is None:
            self.spread_bps = round((self.ask - self.bid) / self.bid * 10_000, 2)
        if self.bid and self.ask and self.mid is None:
            self.mid = round((self.bid + self.ask) / 2, 8)


@dataclass
class ExchangeOrderbook:
    exchange: str
    symbol: str
    timestamp: str
    bids: list = field(default_factory=list)   # [[price, size], ...]
    asks: list = field(default_factory=list)
    depth: int = 20
    imbalance: Optional[float] = None
    spread_bps: Optional[float] = None

    def __post_init__(self) -> None:
        if self.bids and self.asks:
            best_bid = float(self.bids[0][0]) if self.bids else None
            best_ask = float(self.asks[0][0]) if self.asks else None
            if best_bid and best_ask and best_bid > 0 and self.spread_bps is None:
                self.spread_bps = round((best_ask - best_bid) / best_bid * 10_000, 2)
            if self.imbalance is None:
                try:
                    bv = sum(float(b[1]) for b in self.bids[:5])
                    av = sum(float(a[1]) for a in self.asks[:5])
                    tot = bv + av
                    self.imbalance = round(bv / tot - 0.5, 4) if tot > 0 else None
                except Exception:
                    pass


@dataclass
class OrderRequest:
    exchange: str
    symbol: str
    side: str                   # BUY / SELL
    order_type: str             # MARKET / LIMIT
    size: float
    price: Optional[float] = None
    reduce_only: bool = False
    client_order_id: Optional[str] = None
    strategy_id: Optional[str] = None


@dataclass
class OrderResponse:
    exchange: str
    symbol: str
    status: str                 # filled / blocked_live_disabled / error / pending
    order_id: Optional[str] = None
    filled_size: float = 0.0
    avg_price: Optional[float] = None
    raw: dict = field(default_factory=dict)
