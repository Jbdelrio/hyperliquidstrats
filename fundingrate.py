#!/usr/bin/env python3
import argparse
import requests
from datetime import datetime, timezone
from typing import Optional, Dict

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Content-Type": "application/json",
}

def utc(ts, ms=False):
    if ts is None:
        return "N/A"
    if ms:
        ts = int(ts) / 1000
    return datetime.fromtimestamp(int(ts), tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def fetch_binance(coin: str) -> Optional[Dict]:
    symbol = f"{coin}USDT"
    url = "https://fapi.binance.com/fapi/v1/premiumIndex"
    r = requests.get(url, params={"symbol": symbol}, timeout=10)
    r.raise_for_status()
    d = r.json()

    return {
        "exchange": "binance",
        "coin": coin,
        "funding_rate": float(d["lastFundingRate"]),
        "next_funding_time": int(d["nextFundingTime"]),
        "is_ms": True,
    }


def fetch_bitget(coin: str) -> Optional[Dict]:
    symbol = f"{coin}USDT"
    url = "https://api.bitget.com/api/v2/mix/market/current-fund-rate"
    params = {
        "symbol": symbol,
        "productType": "USDT-FUTURES",
    }

    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    d = r.json()

    if d.get("code") != "00000" or not d.get("data"):
        raise ValueError(f"Bitget error: {d}")

    item = d["data"][0]

    return {
        "exchange": "bitget",
        "coin": coin,
        "funding_rate": float(item["fundingRate"]),
        "next_funding_time": int(item["nextUpdate"]),
        "is_ms": True,
    }


def fetch_hyperliquid(coin: str) -> Optional[Dict]:
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "metaAndAssetCtxs"}

    r = requests.post(url, json=payload, headers=HEADERS, timeout=10)
    r.raise_for_status()
    meta, asset_ctxs = r.json()

    universe = meta["universe"]

    for i, asset in enumerate(universe):
        if asset["name"].upper() == coin:
            ctx = asset_ctxs[i]
            return {
                "exchange": "hyperliquid",
                "coin": coin,
                "funding_rate": float(ctx["funding"]),
                # Hyperliquid funding est horaire. Pas toujours de nextFundingTime dans metaAndAssetCtxs.
                "next_funding_time": None,
                "is_ms": False,
            }

    raise ValueError(f"{coin} not found on Hyperliquid")


def fetch_gateio(coin: str) -> Optional[Dict]:
    contract = f"{coin}_USDT"
    url = f"https://api.gateio.ws/api/v4/futures/usdt/contracts/{contract}"

    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    d = r.json()

    return {
        "exchange": "gateio",
        "coin": coin,
        "funding_rate": float(d["funding_rate"]),
        "next_funding_time": int(float(d["funding_next_apply"])),
        "is_ms": False,
    }


def fetch_kraken(coin: str) -> Optional[Dict]:
    # Kraken Futures symbols: BTC = PI_XBTUSD, ETH = PI_ETHUSD, SOL = PF_SOLUSD parfois selon marché.
    symbol_map = {
        "BTC": "PI_XBTUSD",
        "ETH": "PI_ETHUSD",
        "SOL": "PF_SOLUSD",
    }

    target = symbol_map.get(coin)
    if not target:
        raise ValueError(f"Kraken symbol mapping missing for {coin}")

    url = "https://futures.kraken.com/derivatives/api/v3/tickers"
    r = requests.get(url, timeout=10)
    r.raise_for_status()
    d = r.json()

    tickers = d.get("tickers", [])

    for t in tickers:
        if t.get("symbol") == target:
            rate = (
                t.get("fundingRate")
                or t.get("funding_rate")
                or t.get("currentFundingRate")
                or t.get("fundingRatePrediction")
            )

            if rate is None:
                raise ValueError(f"No funding field found in Kraken ticker: {t}")

            return {
                "exchange": "kraken",
                "coin": coin,
                "funding_rate": float(rate),
                "next_funding_time": None,
                "is_ms": False,
            }

    raise ValueError(f"{target} not found on Kraken Futures")


FETCHERS = {
    "binance": fetch_binance,
    "bitget": fetch_bitget,
    "hyperliquid": fetch_hyperliquid,
    "gateio": fetch_gateio,
    "kraken": fetch_kraken,
}


def safe_fetch(exchange: str, coin: str) -> Optional[Dict]:
    try:
        return FETCHERS[exchange](coin)
    except Exception as e:
        print(f"⚠️ {exchange} ({coin}): {str(e)[:160]}")
        return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", required=True, help="Ex: BTC ou BTC,ETH,SOL")
    args = parser.parse_args()

    coins = [c.strip().upper() for c in args.coins.split(",")]
    results = []

    for coin in coins:
        for exchange in FETCHERS:
            res = safe_fetch(exchange, coin)
            if res:
                results.append(res)

    if not results:
        print("❌ Aucun résultat.")
        return

    results.sort(key=lambda x: (x["coin"], x["exchange"]))

    print("\n" + "=" * 100)
    print(f"{'Coin':<8} | {'Exchange':<14} | {'Funding Rate':<16} | {'Next Funding Time UTC'}")
    print("-" * 100)

    for r in results:
        rate = r["funding_rate"]
        icon = "🟢" if rate > 0 else "🔴" if rate < 0 else "⚪"
        time_str = utc(r["next_funding_time"], r["is_ms"]) if r["next_funding_time"] else "N/A"
        print(f"{r['coin']:<8} | {r['exchange']:<14} | {icon} {rate:<14.8f} | {time_str}")

    print("=" * 100)


if __name__ == "__main__":
    main()