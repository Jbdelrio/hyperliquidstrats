"""
hyperliquid_funding.py — Shared Hyperliquid funding rate fetcher.

Hyperliquid API returns funding as the per-8h period rate.
All consumers must use this module so the convention is always consistent.
"""
import logging
from typing import Optional

import requests

log = logging.getLogger(__name__)

_API_URL     = "https://api.hyperliquid.xyz/info"
_API_TIMEOUT = 5.0


def fetch_hyperliquid_funding_rates(coins: Optional[list] = None) -> dict:
    """
    Return {coin: {"raw_8h": float, "hourly_rate": float, "hourly_bps": float, "source": str}}.

    raw_8h        — per-8h period rate as returned by the API
    hourly_rate   — raw_8h / 8
    hourly_bps    — hourly_rate * 10_000

    Returns {} on error.  If `coins` is provided, only include those coins.
    """
    try:
        resp = requests.post(_API_URL, json={"type": "metaAndAssetCtxs"},
                             timeout=_API_TIMEOUT)
        data = resp.json()
        meta, ctxs = data[0], data[1]
        result: dict = {}
        for i, ctx in enumerate(ctxs):
            if i >= len(meta.get("universe", [])):
                break
            coin = meta["universe"][i]["name"]
            if coins is not None and coin not in coins:
                continue
            raw_8h   = float(ctx.get("funding", 0.0))
            hourly   = raw_8h / 8.0
            result[coin] = {
                "raw_8h":      raw_8h,
                "hourly_rate": hourly,
                "hourly_bps":  hourly * 10_000,
                "source":      "hyperliquid_rest",
            }
        return result
    except Exception as exc:
        log.warning("fetch_hyperliquid_funding_rates: %s", exc)
        return {}


def fetch_hyperliquid_funding_and_prices(coins: Optional[list] = None) -> dict:
    """
    Return {coin: {funding_hourly, funding_hourly_bps, oracle_px, mark_px,
                   mid_px, premium, source}}.

    Used by the delta-neutral funding-carry strategy: it needs both the
    funding rate AND a spot reference for the hedge leg.

    Funding convention — IMPORTANT:
      Hyperliquid settles funding **every hour**, and the API `funding`
      field is already the **per-hour** rate (this differs from Binance's
      per-8h convention). So `funding_hourly` is the raw field, untouched.

    Spot leg reference:
      `oracle_px` is Hyperliquid's oracle/index price — the spot reference
      it uses internally to compute funding. It is the natural, self-
      consistent spot price for a Hyperliquid funding carry. `mark_px` is
      the perp mark. basis = (mark_px - oracle_px) / oracle_px.

    Returns {} on error. If `coins` is provided, only include those coins.
    """
    try:
        resp = requests.post(_API_URL, json={"type": "metaAndAssetCtxs"},
                             timeout=_API_TIMEOUT)
        data = resp.json()
        meta, ctxs = data[0], data[1]
        universe = meta.get("universe", [])
        result: dict = {}

        def _f(v) -> float:
            try:
                return float(v)
            except (TypeError, ValueError):
                return 0.0

        for i, ctx in enumerate(ctxs):
            if i >= len(universe):
                break
            coin = universe[i]["name"]
            if coins is not None and coin not in coins:
                continue
            funding_hourly = _f(ctx.get("funding", 0.0))
            result[coin] = {
                "funding_hourly":     funding_hourly,
                "funding_hourly_bps": funding_hourly * 10_000.0,
                "oracle_px":          _f(ctx.get("oraclePx")),
                "mark_px":            _f(ctx.get("markPx")),
                "mid_px":             _f(ctx.get("midPx")),
                "premium":            _f(ctx.get("premium")),
                "source":             "hyperliquid_rest",
            }
        return result
    except Exception as exc:
        log.warning("fetch_hyperliquid_funding_and_prices: %s", exc)
        return {}
