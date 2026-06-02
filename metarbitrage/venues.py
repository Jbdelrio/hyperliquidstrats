"""
metarbitrage/venues.py — venues demandées : Hyperliquid, Aster, PancakeSwap, DexScreener.

Honnêteté instrument :
  - Hyperliquid & Aster = PERPS (type "perp"). Un spread HL-perp vs Aster-perp est
    un vrai spread cross-venue (MÊME instrument) → comparable.
  - PancakeSwap & DexScreener = SPOT DEX (type "spot"). Prix = priceUsd (mid, sans
    profondeur exécutable). Un spread perp↔spot est un BASIS (piloté par le funding),
    PAS un arbitrage atomique capturable.
Le scanner étiquette chaque venue (perp/spot) et marque BASIS les paires de types
différents. Lecture seule.
"""
from __future__ import annotations

import requests

_HL_INFO = "https://api.hyperliquid.xyz/info"
_ASTER = "https://fapi.asterdex.com/fapi/v1/ticker/bookTicker"
_DEXSCR = "https://api.dexscreener.com/latest/dex/search"

VENUE_TYPE = {"hyperliquid": "perp", "aster": "perp",
              "pancakeswap": "spot", "dexscreener": "spot"}
VENUE_FEE = {"hyperliquid": 0.00045, "aster": 0.0005,
             "pancakeswap": 0.0025, "dexscreener": 0.0030}
_QUOTES = ("USDT", "USDC", "BUSD", "USD")


def hyperliquid_tickers() -> dict:
    """{SYM: {bid, ask}} depuis allMids (perp). bid=ask=mid (pas de carnet ici)."""
    try:
        d = requests.post(_HL_INFO, json={"type": "allMids"}, timeout=12).json()
    except Exception:
        return {}
    out = {}
    for coin, mid in d.items():
        if not coin or coin.startswith("@"):     # ignore indices spot HL
            continue
        try:
            m = float(mid)
        except (TypeError, ValueError):
            continue
        if m > 0:
            out[coin.upper()] = {"bid": m, "ask": m}
    return out


def aster_tickers() -> dict:
    """{SYM: {bid, ask}} depuis le bookTicker Aster (perp, bid/ask réels)."""
    try:
        data = requests.get(_ASTER, timeout=12).json()
    except Exception:
        return {}
    if isinstance(data, dict):
        data = [data]
    out = {}
    for t in data:
        sym = str(t.get("symbol", ""))
        base = None
        for q in _QUOTES:
            if sym.endswith(q):
                base = sym[: -len(q)]
                break
        if not base:
            continue
        try:
            bid = float(t.get("bidPrice")); ask = float(t.get("askPrice"))
        except (TypeError, ValueError):
            continue
        if bid > 0 and ask > 0:
            out[base.upper()] = {"bid": bid, "ask": ask}
    return out


_DEXTOK = "https://api.dexscreener.com/latest/dex/tokens/"


def dexscreener_tickers(address_map: dict, pause_s: float = 0.12) -> dict:
    """{venue: {SYM: {bid, ask}}} pour pancakeswap + dexscreener, interrogés par
    ADRESSE de contrat (fiable — pas de collision de symbole/arnaque).

    address_map : {SYMBOL: address}  (adresse de contrat canonique, via CoinGecko).
    On résout par adresse, on prend la paire la plus liquide par dexId (quote stable).
    """
    import time
    if not address_map:
        return {"pancakeswap": {}, "dexscreener": {}}
    addr2sym = {a.lower(): s.upper() for s, a in address_map.items() if a}
    pancake, dexscr = {}, {}
    addrs = list(addr2sym.keys())
    for i in range(0, len(addrs), 25):          # DexScreener accepte ~30 adresses/req
        batch = addrs[i:i + 25]
        try:
            pairs = requests.get(_DEXTOK + ",".join(batch), timeout=15).json().get("pairs", []) or []
        except Exception:
            pairs = []
        # best (liq, px) par (symbol, dexId) et best overall par symbol
        best_dex: dict[tuple, tuple] = {}
        best_all: dict[str, tuple] = {}
        for p in pairs:
            ba = (p.get("baseToken", {}) or {}).get("address", "").lower()
            sym = addr2sym.get(ba)
            quote = (p.get("quoteToken", {}) or {}).get("symbol", "").upper()
            if not sym or quote not in _QUOTES:
                continue
            try:
                px = float(p.get("priceUsd"))
            except (TypeError, ValueError):
                continue
            liq = float((p.get("liquidity", {}) or {}).get("usd", 0) or 0)
            if px <= 0 or liq <= 0:
                continue
            dex = str(p.get("dexId", "")).lower()
            k = (sym, dex)
            if k not in best_dex or liq > best_dex[k][0]:
                best_dex[k] = (liq, px)
            if sym not in best_all or liq > best_all[sym][0]:
                best_all[sym] = (liq, px)
        for (sym, dex), (_, px) in best_dex.items():
            if dex == "pancakeswap":
                pancake[sym] = {"bid": px, "ask": px}
        for sym, (_, px) in best_all.items():
            dexscr[sym] = {"bid": px, "ask": px}
        time.sleep(pause_s)
    return {"pancakeswap": pancake, "dexscreener": dexscr}
