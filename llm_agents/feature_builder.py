"""
llm_agents/feature_builder.py — Build a compact MarketSnapshot from bot data.

Never sends raw dataframes to the LLM. Produces a structured summary
with only the features needed for probabilistic scoring.
"""
from __future__ import annotations

import math
import time
from datetime import datetime, timezone
from typing import Optional

from llm_agents.schemas import MarketSnapshot


def build_market_snapshot(
    symbol: str,
    market_data: dict,
    strategy_outputs: dict,
    account_state: Optional[dict] = None,
    exchange: str = "hyperliquid",
    cross_exchange_data: Optional[dict] = None,
) -> MarketSnapshot:
    """
    Build a MarketSnapshot from available bot data.

    market_data keys (all optional):
        book  : orderbook object with best_bid, best_ask, mid attributes
        bars  : list of dicts {ts, open, high, low, close, volume_usd}

    strategy_outputs : dict mapping strategy_name → StrategyDecision or dict

    account_state keys (all optional):
        equity, open_positions, notional_open, daily_dd_pct
    """
    ts_str = datetime.now(timezone.utc).isoformat()

    book = market_data.get("book")
    bars = market_data.get("bars") or []

    # ── Orderbook features ──────────────────────────────────────────────
    mid_price   = _safe_attr(book, "mid")
    best_bid    = _safe_attr(book, "best_bid")
    best_ask    = _safe_attr(book, "best_ask")
    spread_bps  = _calc_spread_bps(best_bid, best_ask)
    obi         = _calc_orderbook_imbalance(book)

    # ── OHLCV tail (compact, last N bars) ──────────────────────────────
    from llm_agents.config import LLM_MAX_OHLCV_ROWS
    ohlcv_tail = _build_ohlcv_tail(bars, max_rows=min(LLM_MAX_OHLCV_ROWS, 30))

    # ── Volatility ──────────────────────────────────────────────────────
    vol_short, vol_long = _calc_volatility(bars)

    # ── Volume 24h proxy (sum of recent bars) ───────────────────────────
    vol_24h = _sum_volume(bars, n=min(len(bars), 1440))  # up to 24h of 1m bars

    # ── Strategy signals summary ────────────────────────────────────────
    sig_summary = _summarize_strategy_outputs(strategy_outputs)

    # ── Current position ────────────────────────────────────────────────
    current_pos = _extract_position(strategy_outputs)

    # ── Account risk summary ────────────────────────────────────────────
    acc_risk = _extract_account_risk(account_state)

    # ── Available exchanges ──────────────────────────────────────────────
    available_exchanges = [exchange]
    if cross_exchange_data:
        for ex in cross_exchange_data:
            if ex not in available_exchanges:
                available_exchanges.append(ex)

    return MarketSnapshot(
        symbol=symbol,
        timestamp=ts_str,
        exchange=exchange,
        timeframe="1m",
        mid_price=mid_price,
        best_bid=best_bid,
        best_ask=best_ask,
        spread_bps=spread_bps,
        ohlcv_tail=ohlcv_tail,
        funding_rate=market_data.get("funding_rate"),
        open_interest=market_data.get("open_interest"),
        volume_24h=vol_24h,
        volatility_short=vol_short,
        volatility_long=vol_long,
        orderbook_imbalance=obi,
        strategy_signals=sig_summary,
        current_position=current_pos,
        account_risk=acc_risk,
        available_exchanges=available_exchanges,
        cross_exchange_data=cross_exchange_data,
    )


# ── Helpers ─────────────────────────────────────────────────────────────────

def _safe_attr(obj, attr: str):
    if obj is None:
        return None
    v = getattr(obj, attr, None)
    return v if isinstance(v, (int, float)) and math.isfinite(v) else None


def _calc_spread_bps(bid, ask) -> Optional[float]:
    if bid and ask and bid > 0:
        return round((ask - bid) / bid * 10_000, 2)
    return None


def _calc_orderbook_imbalance(book) -> Optional[float]:
    """bid_vol / (bid_vol + ask_vol) - 0.5, in [-0.5, 0.5]."""
    if book is None:
        return None
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return None
    try:
        bid_vol = sum(float(lvl[1]) for lvl in bids[:5] if len(lvl) >= 2)
        ask_vol = sum(float(lvl[1]) for lvl in asks[:5] if len(lvl) >= 2)
        total   = bid_vol + ask_vol
        if total <= 0:
            return None
        return round(bid_vol / total - 0.5, 4)
    except Exception:
        return None


def _build_ohlcv_tail(bars: list, max_rows: int = 20) -> list:
    """Return last max_rows bars as compact dicts."""
    tail = []
    for b in bars[-max_rows:]:
        try:
            tail.append({
                "t": int(b.get("ts", 0)),
                "o": round(float(b["open"]),  6),
                "h": round(float(b["high"]),  6),
                "l": round(float(b["low"]),   6),
                "c": round(float(b["close"]), 6),
                "v": round(float(b.get("volume_usd", 0)), 2),
            })
        except (KeyError, TypeError, ValueError):
            continue
    return tail


def _calc_volatility(bars: list) -> tuple[Optional[float], Optional[float]]:
    """Realized volatility (annualised std of log-returns). short=5, long=20 bars."""
    closes = []
    for b in bars:
        try:
            c = float(b["close"])
            if c > 0:
                closes.append(c)
        except (KeyError, TypeError, ValueError):
            continue

    if len(closes) < 3:
        return None, None

    import math
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]

    def _rv(r_list):
        n = len(r_list)
        if n < 2:
            return None
        mean = sum(r_list) / n
        var  = sum((r - mean) ** 2 for r in r_list) / (n - 1)
        return round(math.sqrt(var) * math.sqrt(525_600), 6)  # annualised from 1m

    vol_short = _rv(rets[-5:])  if len(rets) >= 5  else None
    vol_long  = _rv(rets[-20:]) if len(rets) >= 20 else None
    return vol_short, vol_long


def _sum_volume(bars: list, n: int = 1440) -> Optional[float]:
    total = 0.0
    for b in bars[-n:]:
        try:
            total += float(b.get("volume_usd", 0))
        except (TypeError, ValueError):
            continue
    return round(total, 2) if total > 0 else None


def _summarize_strategy_outputs(outputs: dict) -> dict:
    """Compact summary of each strategy's last signal."""
    result = {}
    for name, dec in outputs.items():
        if hasattr(dec, "action"):
            result[name] = {
                "action":       dec.action,
                "reason":       getattr(dec, "reason", ""),
                "notional_usd": getattr(dec, "notional_usd", None),
            }
        elif isinstance(dec, dict):
            result[name] = {
                "action":       dec.get("action", "UNKNOWN"),
                "reason":       dec.get("reason", ""),
                "notional_usd": dec.get("notional_usd"),
            }
    return result


def _extract_position(outputs: dict) -> Optional[dict]:
    """Placeholder — strategies don't expose position directly here."""
    return None


def _extract_account_risk(account_state: Optional[dict]) -> Optional[dict]:
    if not account_state:
        return None
    return {
        "equity":           account_state.get("equity"),
        "open_positions":   account_state.get("open_positions"),
        "notional_open":    account_state.get("notional_open"),
        "daily_dd_pct":     account_state.get("daily_dd_pct"),
    }
