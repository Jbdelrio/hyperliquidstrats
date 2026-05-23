#!/usr/bin/env python3
import argparse
import requests
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
from collections import defaultdict

# --- Exchange Configurations (OPTIMIZED) ---
ENDPOINTS = {
    "binance": {
        "url": "https://fapi.binance.com/fapi/v1/fundingRate",
        "historical_url": "https://fapi.binance.com/fapi/v1/fundingRate",
        "params": {"symbol": "", "limit": 1},
        "historical_params": {"symbol": "", "limit": 100},
        "symbol_map": lambda c: f"{c}USDT",
        "parse": lambda d: (float(d[0]["fundingRate"]), int(d[0]["fundingTime"])),
        "is_ms": True,
        "unavailable": False,
    },
    "bitget": {
        "url": "https://api.bitget.com/api/v2/mix/market/funding-rate",
        "historical_url": "https://api.bitget.com/api/v2/mix/market/funding-rate-history",
        "params": {"productType": "UMCBL", "symbol": ""},
        "historical_params": {"productType": "UMCBL", "symbol": "", "limit": 100},
        "symbol_map": lambda c: f"{c}_USDT",
        "parse": lambda d: (float(d["data"][0]["fundingRate"]), int(d["data"][0]["fundingTime"])),
        "is_ms": True,
        "unavailable": False,
    },
    "hyperliquid": {
        "url": "https://api.hyperliquid.xyz/info",
        "historical_url": "https://api.hyperliquid.xyz/info",
        "params": {},
        "historical_params": {},
        "symbol_map": lambda c: c,
        "parse": lambda d, coin: (float(d["assets"][coin]["fundingRate"]), int(d["assets"][coin]["nextFundingTime"])),
        "is_ms": False,
        "unavailable": False,
    },
    "kraken": {
        "url": "https://api.kraken.com/0/public/FundingRates",
        "historical_url": "https://api.kraken.com/0/public/FundingRates",
        "params": {"pair": ""},
        "historical_params": {"pair": ""},
        "symbol_map": lambda c: "XBTUSD" if c == "BTC" else f"X{c}USD",
        "parse": lambda d: (float(list(d["result"].values())[0]["rate"]), int(list(d["result"].values())[0]["nextFundingTime"])),
        "is_ms": False,
        "unavailable": False,
    },
    "gateio": {
        "url": "https://api.gateio.ws/api/v4/futures/usdt/funding_rate",
        "historical_url": "https://api.gateio.ws/api/v4/futures/usdt/funding_rate_history",
        "params": {"contract": ""},
        "historical_params": {"contract": "", "limit": 100},
        "symbol_map": lambda c: f"{c}_USDT",
        "parse": lambda d: (float(d[0]["r"]), int(d[0]["t"])),
        "is_ms": False,
        "unavailable": False,
    },
    "phemex": {
        "url": None,
        "unavailable": True,  # Marked as unavailable
    },
    "okx": {
        "url": "https://www.okx.com/api/v5/public/funding-rate",
        "historical_url": "https://www.okx.com/api/v5/public/funding-rate-history",
        "params": {"instId": ""},
        "historical_params": {"instId": "", "limit": 100},
        "symbol_map": lambda c: f"{c}-USDT-SWAP",
        "parse": lambda d: (float(d["data"][0]["fundingRate"]), int(d["data"][0]["fundingTime"])),
        "is_ms": True,
        "unavailable": False,
    },
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

# --- Helper Functions ---
def format_utc(ts: int, is_ms: bool) -> str:
    if is_ms:
        ts = ts / 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

def fetch(exchange: str, coin: str, historical: bool = False) -> Optional[Dict]:
    config = ENDPOINTS[exchange]
    if config.get("unavailable"):
        return None

    symbol = config["symbol_map"](coin)
    url = config["historical_url"] if historical else config["url"]
    params = (config["historical_params"] if historical else config["params"]).copy()

    if "instId" in params:
        params["instId"] = symbol
    elif "symbol" in params:
        params["symbol"] = symbol
    elif "contract" in params:
        params["contract"] = symbol
    elif "pair" in params:
        params["pair"] = symbol

    try:
        time.sleep(0.2)
        response = requests.get(url, params=params, headers=HEADERS, timeout=10)
        if response.status_code != 200:
            return None

        data = response.json()
        if exchange == "hyperliquid":
            rate, next_time = config["parse"](data, coin)
        else:
            rate, next_time = config["parse"](data)

        if historical:
            return {
                "exchange": exchange,
                "coin": coin,
                "rate": rate,
                "time": next_time,
                "is_ms": config["is_ms"],
            }
        else:
            return {
                "exchange": exchange,
                "coin": coin,
                "rate": rate,
                "next_time": next_time,
                "is_ms": config["is_ms"],
            }
    except Exception:
        return None

# --- Display Current Rates ---
def display_current(results: List[Dict]):
    if not results:
        print("❌ No current funding rates found.")
        return

    results.sort(key=lambda x: (x["coin"], x["exchange"]))
    print("\n" + "=" * 90)
    print("📊 CURRENT FUNDING RATES (UTC)")
    print("-" * 90)
    print(f"{'Coin':<8} | {'Exchange':<12} | {'Funding Rate':<15} | {'Next Funding (UTC)'}")
    print("-" * 90)

    for r in results:
        rate = r["rate"]
        color = "🟢" if rate > 0 else "🔴" if rate < 0 else "⚪"
        time_str = format_utc(r["next_time"], r["is_ms"])
        exchange_name = f"{r['exchange'].upper()}*" if r["exchange"] == "phemex" else r["exchange"].upper()
        print(f"{r['coin']:<8} | {exchange_name:<12} | {color} {rate:<15.6f} | {time_str}")
    print("=" * 90)
    print("(*) Phemex API currently unavailable")

# --- Arbitrage Mode ---
def calculate_arbitrage(results: List[Dict]) -> List[Dict]:
    opportunities = []
    coins = defaultdict(list)
    for r in results:
        coins[r["coin"]].append(r)

    for coin, data in coins.items():
        if len(data) < 2:
            continue
        data.sort(key=lambda x: x["rate"])
        min_rate, max_rate = data[0], data[-1]
        spread = max_rate["rate"] - min_rate["rate"]
        if spread > 0:
            opportunities.append({
                "coin": coin,
                "buy_exchange": min_rate["exchange"],
                "sell_exchange": max_rate["exchange"],
                "spread_bps": spread * 10000,
                "buy_rate": min_rate["rate"],
                "sell_rate": max_rate["rate"],
                "next_funding_time": min(min_rate["next_time"], max_rate["next_time"]),
                "is_ms": min_rate["is_ms"] or max_rate["is_ms"],
            })

    opportunities.sort(key=lambda x: x["spread_bps"], reverse=True)
    return opportunities

def display_arbitrage(opportunities: List[Dict]):
    if not opportunities:
        print("❌ No arbitrage opportunities found.")
        return

    print("\n" + "=" * 100)
    print("🔄 FUNDING RATE ARBITRAGE OPPORTUNITIES (UTC)")
    print("Strategy: Long on low funding rate exchange | Short on high funding rate exchange")
    print("-" * 100)
    print(f"{'Coin':<8} | {'Buy':<12} | {'Sell':<12} | {'Spread (bps)':<12} | {'Buy Rate':<15} | {'Sell Rate':<15}")
    print("-" * 100)

    for opp in opportunities:
        print(f"{opp['coin']:<8} | {opp['buy_exchange'].upper():<12} | {opp['sell_exchange'].upper():<12} | {opp['spread_bps']:<12.2f} | {opp['buy_rate']:<15.6f} | {opp['sell_rate']:<15.6f}")
    print("=" * 100)

# --- NEW: Compare Mode ---
def calculate_comparison(results: List[Dict]) -> Dict:
    comparison = defaultdict(dict)
    for r in results:
        comparison[r["coin"]][r["exchange"]] = {
            "rate": r["rate"],
            "next_time": r["next_time"],
            "is_ms": r["is_ms"],
        }
    return comparison

def display_comparison(comparison: Dict):
    if not comparison:
        print("❌ No data to compare.")
        return

    print("\n" + "=" * 120)
    print("🔍 EXCHANGE COMPARISON (UTC) - Funding Rate Differences")
    print("-" * 120)

    for coin, exchanges in comparison.items():
        print(f"\n🪙 {coin} (Next Funding: {format_utc(min(e['next_time'] for e in exchanges.values()), next(iter(exchanges.values()))['is_ms'])} UTC)")
        print("-" * 80)

        # Sort exchanges by rate
        sorted_exchanges = sorted(exchanges.items(), key=lambda x: x[1]["rate"])

        # Create comparison table
        print(f"{'Exchange':<12} | {'Funding Rate':<15} | {'vs Avg':<15} | {'vs Min':<15} | {'vs Max':<15}")
        print("-" * 80)

        rates = [e["rate"] for e in exchanges.values()]
        avg_rate = sum(rates) / len(rates)
        min_rate = min(rates)
        max_rate = max(rates)

        for exchange, data in sorted_exchanges:
            rate = data["rate"]
            color = "🟢" if rate > 0 else "🔴" if rate < 0 else "⚪"
            vs_avg = rate - avg_rate
            vs_min = rate - min_rate
            vs_max = rate - max_rate
            exchange_name = f"{exchange.upper()}*" if exchange == "phemex" else exchange.upper()
            print(f"{exchange_name:<12} | {color} {rate:<15.6f} | {vs_avg:+<15.6f} | {vs_min:+<15.6f} | {vs_max:+<15.6f}")

        # Summary
        print(f"\n  📌 Summary for {coin}:")
        print(f"    Average: {avg_rate:.6f} | Min: {min_rate:.6f} ({sorted_exchanges[0][0].upper()}) | Max: {max_rate:.6f} ({sorted_exchanges[-1][0].upper()})")
        print(f"    Spread: {max_rate - min_rate:.6f} ({((max_rate - min_rate)*10000):.2f} bps)")

    print("\n" + "=" * 120)

# --- Historical Mode ---
def display_historical(all_historical: Dict[str, Dict[str, List[Dict]]]):
    if not all_historical:
        print("❌ No historical data found.")
        return

    print("\n" + "=" * 90)
    print("📈 FUNDING RATES HISTORY (Last 24h, UTC)")
    print("-" * 90)

    for coin, exchanges in all_historical.items():
        print(f"\n🪙 {coin}")
        print("-" * 40)
        for exchange, rates in exchanges.items():
            if not rates:
                continue
            exchange_name = exchange.upper() + "*" if exchange == "phemex" else exchange.upper()
            print(f"  {exchange_name}:")
            rates.sort(key=lambda x: x["time"], reverse=True)
            for r in rates[:5]:
                time_str = format_utc(r["time"], r["is_ms"])
                color = "🟢" if r["rate"] > 0 else "🔴" if r["rate"] < 0 else "⚪"
                print(f"    {time_str} | {color} {r['rate']:.6f}")
    print("=" * 90)

# --- Main ---
def main():
    parser = argparse.ArgumentParser(
        description="Tool to display funding rates, arbitrage opportunities, and exchange comparisons.",
        formatter_class=argparse.RawTextHelpFormatter,
    )
    parser.add_argument(
        "--coins",
        type=str,
        default="BTC",
        help="Coins to analyze (comma-separated). Example: BTC,ETH,SOL (default: BTC)",
    )
    parser.add_argument(
        "--exchanges",
        type=str,
        default="all",
        help="Exchanges to use (comma-separated). Use 'all' for all exchanges (default: all).\n"
             "Available: binance, bitget, hyperliquid, kraken, gateio, okx",
    )
    parser.add_argument(
        "--historical",
        action="store_true",
        help="Display historical funding rates (last 24h).",
    )
    parser.add_argument(
        "--arbitrage",
        action="store_true",
        help="Display arbitrage opportunities between exchanges.",
    )
    parser.add_argument(
        "--compare",
        action="store_true",
        help="Display detailed comparison between all exchanges for each coin.",
    )
    args = parser.parse_args()

    # --- Setup ---
    coins = [c.strip().upper() for c in args.coins.split(",")]
    selected_exchanges = [e.strip().lower() for e in args.exchanges.split(",")]
    if "all" in selected_exchanges:
        exchanges = [e for e in ENDPOINTS if not ENDPOINTS[e].get("unavailable")]
    else:
        exchanges = [e for e in selected_exchanges if e in ENDPOINTS and not ENDPOINTS[e].get("unavailable")]
        if not exchanges:
            print("❌ No valid exchanges selected. Use --exchanges all or specify valid exchanges.")
            return

    # --- Fetch Data ---
    all_results = []
    all_historical = {coin: {} for coin in coins}

    for coin in coins:
        for exchange in exchanges:
            result = fetch(exchange, coin)
            if result:
                all_results.append(result)

            if args.historical:
                historical = fetch(exchange, coin, historical=True)
                if historical:
                    all_historical[coin][exchange] = [historical]  # Simplified for this example

    # --- Display Results ---
    display_current(all_results)

    if args.arbitrage:
        arbitrage_opps = calculate_arbitrage(all_results)
        display_arbitrage(arbitrage_opps)

    if args.compare:
        comparison_data = calculate_comparison(all_results)
        display_comparison(comparison_data)

    if args.historical:
        display_historical(all_historical)

if __name__ == "__main__":
    main()