"""
Per-exchange adapter functions. Three categories:

1. Per-coin: ``async def fetch_<exchange>(session, coin)`` -> Optional[dict]
2. Bulk:     ``async def list_<exchange>(session)`` -> list[dict]
3. History:  ``async def history_<exchange>(session, symbol, limit)`` -> list[dict]
4. Detail:   ``async def detail_<exchange>(session, symbol)`` -> Optional[dict]

Funding rates are decimals (0.0001 = 1 bp); times are unix epoch milliseconds.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Awaitable, Callable, Dict, List, Optional

import aiohttp

from .settings import REQUEST_TIMEOUT, USER_AGENT


HEADERS = {"User-Agent": USER_AGENT, "Accept": "application/json"}
TIMEOUT = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)


def _result(exchange: str, coin: str, rate: float, next_time_ms: int) -> Dict:
    return {
        "exchange": exchange,
        "coin": coin,
        "rate": float(rate),
        "next_time_ms": int(next_time_ms),
        "fetched_at_ms": int(time.time() * 1000),
        "available": True,
    }


async def _get_json(session: aiohttp.ClientSession, url: str, params: Optional[Dict] = None) -> Optional[Dict]:
    async with session.get(url, params=params, headers=HEADERS, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            return None
        return await resp.json(content_type=None)


async def _post_json(session: aiohttp.ClientSession, url: str, body: Dict) -> Optional[Dict]:
    async with session.post(url, json=body, headers=HEADERS, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            return None
        return await resp.json(content_type=None)


def _iso_to_ms(s: str) -> int:
    if not s:
        return 0
    s = s.replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(s).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def _detail(exchange: str, symbol: str, rate: float, next_time_ms: int,
            **extras) -> Dict:
    return {
        "exchange": exchange,
        "symbol": symbol,
        "rate": float(rate),
        "next_time_ms": int(next_time_ms),
        "fetched_at_ms": int(time.time() * 1000),
        "mark_price": extras.get("mark_price"),
        "index_price": extras.get("index_price"),
        "open_interest": extras.get("open_interest"),
        "volume_24h": extras.get("volume_24h"),
        "price_change_pct_24h": extras.get("price_change_pct_24h"),
        "last_price": extras.get("last_price"),
        "interval_hours": extras.get("interval_hours", 8),
    }


def _hist(timestamp_ms: int, rate: float) -> Dict:
    return {"timestamp_ms": int(timestamp_ms), "rate": float(rate)}


# ---- Binance ---------------------------------------------------------------
async def fetch_binance(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    data = await _get_json(
        session,
        "https://fapi.binance.com/fapi/v1/premiumIndex",
        params={"symbol": f"{coin}USDT"},
    )
    if not data or "lastFundingRate" not in data:
        return None
    return _result("binance", coin, data["lastFundingRate"], data["nextFundingTime"])


# ---- AsterDEX (Binance-compatible fork) -----------------------------------
async def fetch_aster(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    data = await _get_json(
        session,
        "https://fapi.asterdex.com/fapi/v1/premiumIndex",
        params={"symbol": f"{coin}USDT"},
    )
    if not data or "lastFundingRate" not in data:
        return None
    return _result("aster", coin, data["lastFundingRate"], data["nextFundingTime"])


# ---- Bitget v2 -------------------------------------------------------------
async def fetch_bitget(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    symbol = f"{coin}USDT"
    rate_data = await _get_json(
        session,
        "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
        params={"symbol": symbol, "productType": "USDT-FUTURES"},
    )
    if not rate_data or rate_data.get("code") != "00000" or not rate_data.get("data"):
        return None
    rate = float(rate_data["data"][0]["fundingRate"])

    time_data = await _get_json(
        session,
        "https://api.bitget.com/api/v2/mix/market/funding-time",
        params={"symbol": symbol, "productType": "USDT-FUTURES"},
    )
    next_time_ms = 0
    if time_data and time_data.get("code") == "00000" and time_data.get("data"):
        try:
            next_time_ms = int(time_data["data"][0]["nextFundingTime"])
        except (KeyError, ValueError, TypeError):
            next_time_ms = 0
    return _result("bitget", coin, rate, next_time_ms)


# ---- OKX -------------------------------------------------------------------
async def fetch_okx(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    data = await _get_json(
        session,
        "https://www.okx.com/api/v5/public/funding-rate",
        params={"instId": f"{coin}-USDT-SWAP"},
    )
    if not data or data.get("code") != "0" or not data.get("data"):
        return None
    row = data["data"][0]
    return _result("okx", coin, row["fundingRate"], row["nextFundingTime"])


# ---- GateIO ----------------------------------------------------------------
async def fetch_gateio(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    contract = f"{coin}_USDT"
    data = await _get_json(
        session,
        f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}",
    )
    if not data or "funding_rate" not in data:
        return None
    rate = float(data["funding_rate"])
    next_time_s = int(data.get("funding_next_apply", 0))
    return _result("gateio", coin, rate, next_time_s * 1000)


# ---- Hyperliquid (POST) ----------------------------------------------------
async def fetch_hyperliquid(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    data = await _post_json(
        session,
        "https://api.hyperliquid.xyz/info",
        body={"type": "metaAndAssetCtxs"},
    )
    if not data or not isinstance(data, list) or len(data) < 2:
        return None
    try:
        universe = data[0]["universe"]
        ctxs = data[1]
        for idx, asset in enumerate(universe):
            if asset.get("name") == coin:
                rate = float(ctxs[idx]["funding"])
                # Hyperliquid funds hourly — next funding = top of next UTC hour
                now_ms = int(time.time() * 1000)
                next_hour_ms = ((now_ms // 3_600_000) + 1) * 3_600_000
                return _result("hyperliquid", coin, rate, next_hour_ms)
    except (KeyError, IndexError, ValueError, TypeError):
        return None
    return None


# ---- Kraken Futures --------------------------------------------------------
_KRAKEN_SYMBOL = {"BTC": "PF_XBTUSD", "XBT": "PF_XBTUSD"}


async def fetch_kraken(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    symbol = _KRAKEN_SYMBOL.get(coin, f"PF_{coin}USD")
    # historicalfundingrates returns a clean fractional rate; the /tickers field
    # confusingly mixes USD-per-contract into the same key name.
    data = await _get_json(
        session,
        "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates",
        params={"symbol": symbol},
    )
    if not data or not data.get("rates"):
        return None
    last = data["rates"][-1]
    rate = last.get("relativeFundingRate")
    if rate is None:
        return None
    # Kraken Futures funds hourly
    now_ms = int(time.time() * 1000)
    next_hour_ms = ((now_ms // 3_600_000) + 1) * 3_600_000
    return _result("kraken", coin, rate, next_hour_ms)


# ---- Bitmex ----------------------------------------------------------------
def _bitmex_symbol(coin: str) -> str:
    if coin.upper() in ("BTC", "XBT"):
        return "XBTUSDT"
    return f"{coin.upper()}USDT"


async def fetch_bitmex(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    symbol = _bitmex_symbol(coin)
    data = await _get_json(
        session,
        "https://www.bitmex.com/api/v1/instrument",
        params={"symbol": symbol},
    )
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    rate = row.get("fundingRate")
    if rate is None:
        return None
    next_ms = _iso_to_ms(row.get("fundingTimestamp", ""))
    return _result("bitmex", coin, rate, next_ms)


# ---- Phemex (often returns 500 — kept for completeness) --------------------
async def fetch_phemex(session: aiohttp.ClientSession, coin: str) -> Optional[Dict]:
    symbol = f".{coin}USDFR"  # funding-rate symbol per Phemex docs
    data = await _get_json(
        session,
        "https://api.phemex.com/md/ticker/24hr",
        params={"symbol": symbol},
    )
    if not data or data.get("error") or "result" not in data:
        return None
    try:
        # Phemex returns fundingRate scaled by 1e8
        rate = float(data["result"].get("indexPriceEp", 0)) / 1e8
        now_ms = int(time.time() * 1000)
        next_8h_ms = ((now_ms // 28_800_000) + 1) * 28_800_000
        return _result("phemex", coin, rate, next_8h_ms)
    except (KeyError, ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Bulk endpoints — list ALL perpetuals for an exchange in a single call.
# Returns a list of rows shaped like _result() with "coin" set to the full
# perp symbol (e.g. "BTCUSDT", "PEPE_USDT", "kPEPE").
# ---------------------------------------------------------------------------

def _bulk_row(exchange: str, symbol: str, rate: float, next_time_ms: int) -> Dict:
    row = _result(exchange, symbol, rate, next_time_ms)
    row["symbol"] = symbol
    return row


async def list_binance(session: aiohttp.ClientSession) -> list[Dict]:
    return await _list_binance_like(session, "binance",
                                    "https://fapi.binance.com/fapi/v1/premiumIndex")


async def list_aster(session: aiohttp.ClientSession) -> list[Dict]:
    return await _list_binance_like(session, "aster",
                                    "https://fapi.asterdex.com/fapi/v1/premiumIndex")


async def _list_binance_like(session: aiohttp.ClientSession, exchange: str, url: str) -> list[Dict]:
    data = await _get_json(session, url)
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        try:
            symbol = item["symbol"]
            rate = float(item["lastFundingRate"])
            nt = int(item["nextFundingTime"])
            out.append(_bulk_row(exchange, symbol, rate, nt))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def list_bitget(session: aiohttp.ClientSession) -> list[Dict]:
    data = await _get_json(
        session,
        "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
        params={"productType": "USDT-FUTURES"},
    )
    if not data or data.get("code") != "00000" or not isinstance(data.get("data"), list):
        return []
    # Bitget bulk endpoint omits nextFundingTime — use the synced 8h boundary
    now_ms = int(time.time() * 1000)
    next_8h_ms = ((now_ms // 28_800_000) + 1) * 28_800_000
    out = []
    for item in data["data"]:
        try:
            out.append(_bulk_row("bitget", item["symbol"], float(item["fundingRate"]), next_8h_ms))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def list_gateio(session: aiohttp.ClientSession) -> list[Dict]:
    data = await _get_json(session, "https://api.gateio.ws/api/v4/futures/usdt/contracts")
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        try:
            rate = float(item.get("funding_rate", 0))
            nt = int(item.get("funding_next_apply", 0)) * 1000
            out.append(_bulk_row("gateio", item["name"], rate, nt))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def list_bitmex(session: aiohttp.ClientSession) -> list[Dict]:
    data = await _get_json(session, "https://www.bitmex.com/api/v1/instrument/active")
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if item.get("typ") != "FFWCSX":  # perpetual swaps only
            continue
        rate = item.get("fundingRate")
        if rate is None:
            continue
        next_ms = _iso_to_ms(item.get("fundingTimestamp", ""))
        try:
            out.append(_bulk_row("bitmex", item["symbol"], float(rate), next_ms))
        except (KeyError, TypeError, ValueError):
            continue
    return out


async def list_hyperliquid(session: aiohttp.ClientSession) -> list[Dict]:
    data = await _post_json(session, "https://api.hyperliquid.xyz/info",
                            body={"type": "metaAndAssetCtxs"})
    if not isinstance(data, list) or len(data) < 2:
        return []
    universe = data[0].get("universe", [])
    ctxs = data[1]
    now_ms = int(time.time() * 1000)
    next_hour_ms = ((now_ms // 3_600_000) + 1) * 3_600_000
    out = []
    for idx, asset in enumerate(universe):
        try:
            rate = float(ctxs[idx]["funding"])
            out.append(_bulk_row("hyperliquid", asset["name"], rate, next_hour_ms))
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    return out


# ---------------------------------------------------------------------------
# HISTORY adapters — return list[{"timestamp_ms", "rate"}], newest first.
# ---------------------------------------------------------------------------

async def history_binance(session, symbol, limit=50) -> List[Dict]:
    return await _history_binance_like(session,
        "https://fapi.binance.com/fapi/v1/fundingRate", symbol, limit)


async def history_aster(session, symbol, limit=50) -> List[Dict]:
    return await _history_binance_like(session,
        "https://fapi.asterdex.com/fapi/v1/fundingRate", symbol, limit)


async def _history_binance_like(session, url, symbol, limit) -> List[Dict]:
    data = await _get_json(session, url,
                           params={"symbol": symbol, "limit": min(limit, 1000)})
    if not isinstance(data, list):
        return []
    out = [_hist(int(d["fundingTime"]), float(d["fundingRate"]))
           for d in data if "fundingTime" in d and "fundingRate" in d]
    out.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    return out[:limit]


async def history_bitget(session, symbol, limit=50) -> List[Dict]:
    data = await _get_json(session,
        "https://api.bitget.com/api/v2/mix/market/history-fund-rate",
        params={"symbol": symbol, "productType": "USDT-FUTURES",
                "pageSize": min(limit, 100)})
    if not data or data.get("code") != "00000" or not isinstance(data.get("data"), list):
        return []
    out = []
    for d in data["data"]:
        try:
            out.append(_hist(int(d["fundingTime"]), float(d["fundingRate"])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    return out[:limit]


async def history_okx(session, symbol, limit=50) -> List[Dict]:
    data = await _get_json(session,
        "https://www.okx.com/api/v5/public/funding-rate-history",
        params={"instId": symbol, "limit": min(limit, 100)})
    if not data or data.get("code") != "0":
        return []
    out = []
    for d in data.get("data", []):
        try:
            out.append(_hist(int(d["fundingTime"]),
                             float(d.get("realizedRate", d.get("fundingRate", 0)))))
        except (KeyError, TypeError, ValueError):
            continue
    return out[:limit]


async def history_gateio(session, symbol, limit=50) -> List[Dict]:
    data = await _get_json(session,
        "https://api.gateio.ws/api/v4/futures/usdt/funding_rate",
        params={"contract": symbol, "limit": min(limit, 1000)})
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        try:
            out.append(_hist(int(d["t"]) * 1000, float(d["r"])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    return out[:limit]


async def history_hyperliquid(session, symbol, limit=50) -> List[Dict]:
    # /info fundingHistory needs startTime; estimate (limit + buffer) hours back
    start_ms = int(time.time() * 1000) - (limit + 12) * 3_600_000
    data = await _post_json(session, "https://api.hyperliquid.xyz/info",
        body={"type": "fundingHistory", "coin": symbol, "startTime": start_ms})
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        try:
            out.append(_hist(int(d["time"]), float(d["fundingRate"])))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    return out[:limit]


async def history_kraken(session, symbol, limit=50) -> List[Dict]:
    data = await _get_json(session,
        "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates",
        params={"symbol": symbol})
    if not data or not isinstance(data.get("rates"), list):
        return []
    out = []
    for d in data["rates"]:
        try:
            out.append(_hist(_iso_to_ms(d["timestamp"]),
                             float(d.get("relativeFundingRate", 0))))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: r["timestamp_ms"], reverse=True)
    return out[:limit]


async def history_bitmex(session, symbol, limit=50) -> List[Dict]:
    data = await _get_json(session, "https://www.bitmex.com/api/v1/funding",
        params={"symbol": symbol, "count": min(limit, 500), "reverse": "true"})
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        try:
            out.append(_hist(_iso_to_ms(d["timestamp"]), float(d["fundingRate"])))
        except (KeyError, TypeError, ValueError):
            continue
    return out[:limit]


# ---------------------------------------------------------------------------
# DETAIL adapters — 2 calls combined: funding endpoint + 24h ticker.
# ---------------------------------------------------------------------------

async def detail_binance(session, symbol) -> Optional[Dict]:
    return await _detail_binance_like(session, "binance",
        "https://fapi.binance.com/fapi/v1", symbol)


async def detail_aster(session, symbol) -> Optional[Dict]:
    return await _detail_binance_like(session, "aster",
        "https://fapi.asterdex.com/fapi/v1", symbol)


async def _detail_binance_like(session, exchange, base, symbol) -> Optional[Dict]:
    import asyncio as _aio
    pi_url = f"{base}/premiumIndex"
    tk_url = f"{base}/ticker/24hr"
    pi, tk = await _aio.gather(
        _get_json(session, pi_url, params={"symbol": symbol}),
        _get_json(session, tk_url, params={"symbol": symbol}),
        return_exceptions=True,
    )
    if isinstance(pi, Exception) or not pi or "lastFundingRate" not in pi:
        return None
    tk = tk if isinstance(tk, dict) else {}
    return _detail(exchange, symbol,
        rate=float(pi["lastFundingRate"]),
        next_time_ms=int(pi["nextFundingTime"]),
        mark_price=float(pi.get("markPrice", 0)) or None,
        index_price=float(pi.get("indexPrice", 0)) or None,
        last_price=float(tk.get("lastPrice", 0)) or None,
        volume_24h=float(tk.get("quoteVolume", 0)) or None,
        price_change_pct_24h=float(tk.get("priceChangePercent", 0)) or None,
    )


async def detail_bitget(session, symbol) -> Optional[Dict]:
    import asyncio as _aio
    fr, tk = await _aio.gather(
        _get_json(session,
            "https://api.bitget.com/api/v2/mix/market/current-fund-rate",
            params={"symbol": symbol, "productType": "USDT-FUTURES"}),
        _get_json(session,
            "https://api.bitget.com/api/v2/mix/market/ticker",
            params={"symbol": symbol, "productType": "USDT-FUTURES"}),
        return_exceptions=True,
    )
    if isinstance(fr, Exception) or not fr or fr.get("code") != "00000":
        return None
    rate = float(fr["data"][0]["fundingRate"])
    t = tk["data"][0] if isinstance(tk, dict) and tk.get("data") else {}
    ft = await _get_json(session,
        "https://api.bitget.com/api/v2/mix/market/funding-time",
        params={"symbol": symbol, "productType": "USDT-FUTURES"})
    next_ms = 0
    if ft and ft.get("data"):
        try: next_ms = int(ft["data"][0]["nextFundingTime"])
        except (KeyError, TypeError, ValueError): pass
    return _detail("bitget", symbol, rate=rate, next_time_ms=next_ms,
        mark_price=float(t.get("markPrice", 0) or 0) or None,
        index_price=float(t.get("indexPrice", 0) or 0) or None,
        last_price=float(t.get("lastPr", 0) or 0) or None,
        volume_24h=float(t.get("usdtVolume", 0) or 0) or None,
        price_change_pct_24h=float(t.get("change24h", 0) or 0) * 100 or None,
        open_interest=float(t.get("holdingAmount", 0) or 0) or None,
    )


async def detail_okx(session, symbol) -> Optional[Dict]:
    import asyncio as _aio
    fr, tk = await _aio.gather(
        _get_json(session, "https://www.okx.com/api/v5/public/funding-rate",
            params={"instId": symbol}),
        _get_json(session, "https://www.okx.com/api/v5/market/ticker",
            params={"instId": symbol}),
        return_exceptions=True,
    )
    if isinstance(fr, Exception) or not fr or fr.get("code") != "0":
        return None
    row = fr["data"][0]
    t = tk["data"][0] if isinstance(tk, dict) and tk.get("data") else {}
    last = float(t.get("last", 0) or 0) or None
    open_ = float(t.get("open24h", 0) or 0) or None
    pct = ((last - open_) / open_ * 100) if last and open_ else None
    return _detail("okx", symbol,
        rate=float(row["fundingRate"]),
        next_time_ms=int(row["nextFundingTime"]),
        mark_price=float(t.get("markPx", 0) or 0) or None,
        index_price=float(t.get("idxPx", 0) or 0) or None,
        last_price=last,
        volume_24h=float(t.get("volCcy24h", 0) or 0) or None,
        price_change_pct_24h=pct,
    )


async def detail_gateio(session, symbol) -> Optional[Dict]:
    import asyncio as _aio
    contract, ticker = await _aio.gather(
        _get_json(session, f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{symbol}"),
        _get_json(session, "https://api.gateio.ws/api/v4/futures/usdt/tickers",
                  params={"contract": symbol}),
        return_exceptions=True,
    )
    if isinstance(contract, Exception) or not contract or "funding_rate" not in contract:
        return None
    t = ticker[0] if isinstance(ticker, list) and ticker else {}
    return _detail("gateio", symbol,
        rate=float(contract["funding_rate"]),
        next_time_ms=int(contract.get("funding_next_apply", 0)) * 1000,
        mark_price=float(contract.get("mark_price", 0) or 0) or None,
        index_price=float(contract.get("index_price", 0) or 0) or None,
        last_price=float(t.get("last", 0) or 0) or None,
        volume_24h=float(t.get("volume_24h_quote", 0) or 0) or None,
        price_change_pct_24h=float(t.get("change_percentage", 0) or 0) or None,
        open_interest=float(t.get("total_size", 0) or 0) or None,
    )


async def detail_hyperliquid(session, symbol) -> Optional[Dict]:
    data = await _post_json(session, "https://api.hyperliquid.xyz/info",
                            body={"type": "metaAndAssetCtxs"})
    if not isinstance(data, list) or len(data) < 2:
        return None
    universe, ctxs = data[0].get("universe", []), data[1]
    for idx, asset in enumerate(universe):
        if asset.get("name") == symbol:
            c = ctxs[idx]
            now_ms = int(time.time() * 1000)
            next_hour = ((now_ms // 3_600_000) + 1) * 3_600_000
            return _detail("hyperliquid", symbol,
                rate=float(c["funding"]),
                next_time_ms=next_hour,
                mark_price=float(c.get("markPx", 0) or 0) or None,
                index_price=float(c.get("oraclePx", 0) or 0) or None,
                open_interest=float(c.get("openInterest", 0) or 0) or None,
                volume_24h=float(c.get("dayNtlVlm", 0) or 0) or None,
                interval_hours=1,
            )
    return None


async def detail_kraken(session, symbol) -> Optional[Dict]:
    import asyncio as _aio
    hist, tickers = await _aio.gather(
        _get_json(session,
            "https://futures.kraken.com/derivatives/api/v4/historicalfundingrates",
            params={"symbol": symbol}),
        _get_json(session,
            "https://futures.kraken.com/derivatives/api/v3/tickers"),
        return_exceptions=True,
    )
    if isinstance(hist, Exception) or not hist or not hist.get("rates"):
        return None
    rate = float(hist["rates"][-1].get("relativeFundingRate") or 0)
    t = {}
    if isinstance(tickers, dict):
        for row in tickers.get("tickers", []):
            if row.get("symbol") == symbol:
                t = row; break
    now_ms = int(time.time() * 1000)
    next_hour = ((now_ms // 3_600_000) + 1) * 3_600_000
    return _detail("kraken", symbol, rate=rate, next_time_ms=next_hour,
        mark_price=float(t.get("markPrice", 0) or 0) or None,
        index_price=float(t.get("indexPrice", 0) or 0) or None,
        last_price=float(t.get("last", 0) or 0) or None,
        volume_24h=float(t.get("vol24h", 0) or 0) or None,
        price_change_pct_24h=float(t.get("change24h", 0) or 0) or None,
        open_interest=float(t.get("openInterest", 0) or 0) or None,
        interval_hours=1,
    )


async def detail_bitmex(session, symbol) -> Optional[Dict]:
    data = await _get_json(session, "https://www.bitmex.com/api/v1/instrument",
                           params={"symbol": symbol})
    if not isinstance(data, list) or not data:
        return None
    row = data[0]
    last = float(row.get("lastPrice", 0) or 0) or None
    prev = float(row.get("prevPrice24h", 0) or 0) or None
    pct = ((last - prev) / prev * 100) if last and prev else None
    return _detail("bitmex", symbol,
        rate=float(row.get("fundingRate") or 0),
        next_time_ms=_iso_to_ms(row.get("fundingTimestamp", "")),
        mark_price=float(row.get("markPrice", 0) or 0) or None,
        index_price=float(row.get("indicativeSettlePrice", 0) or 0) or None,
        last_price=last,
        volume_24h=float(row.get("volume24h", 0) or 0) or None,
        price_change_pct_24h=pct,
        open_interest=float(row.get("openInterest", 0) or 0) or None,
    )


# ---- Registry --------------------------------------------------------------
Adapter = Callable[[aiohttp.ClientSession, str], Awaitable[Optional[Dict]]]
BulkAdapter = Callable[[aiohttp.ClientSession], Awaitable[List[Dict]]]
HistoryAdapter = Callable[[aiohttp.ClientSession, str, int], Awaitable[List[Dict]]]
DetailAdapter = Callable[[aiohttp.ClientSession, str], Awaitable[Optional[Dict]]]

EXCHANGES: Dict[str, Adapter] = {
    "binance": fetch_binance,
    "aster": fetch_aster,
    "bitget": fetch_bitget,
    "okx": fetch_okx,
    "gateio": fetch_gateio,
    "hyperliquid": fetch_hyperliquid,
    "kraken": fetch_kraken,
    "bitmex": fetch_bitmex,
    "phemex": fetch_phemex,
}

BULK_EXCHANGES: Dict[str, BulkAdapter] = {
    "binance": list_binance,
    "aster": list_aster,
    "bitget": list_bitget,
    "gateio": list_gateio,
    "hyperliquid": list_hyperliquid,
    "bitmex": list_bitmex,
}

HISTORY_EXCHANGES: Dict[str, HistoryAdapter] = {
    "binance": history_binance,
    "aster": history_aster,
    "bitget": history_bitget,
    "okx": history_okx,
    "gateio": history_gateio,
    "hyperliquid": history_hyperliquid,
    "kraken": history_kraken,
    "bitmex": history_bitmex,
}

DETAIL_EXCHANGES: Dict[str, DetailAdapter] = {
    "binance": detail_binance,
    "aster": detail_aster,
    "bitget": detail_bitget,
    "okx": detail_okx,
    "gateio": detail_gateio,
    "hyperliquid": detail_hyperliquid,
    "kraken": detail_kraken,
    "bitmex": detail_bitmex,
}

# Per-exchange min delay between calls (seconds). Default falls back to RATE_LIMIT_DELAY.
EXCHANGE_DELAYS: Dict[str, float] = {
    "bitmex": 2.0,   # public unauth: 30 req/min
    "kraken": 1.0,   # 1 req/sec is safe for public futures
}

# Exchanges known to be flaky — marked with "*" in displays but still attempted.
FLAKY = {"phemex"}
