"""
calibrate_har_rv.py — Monthly HAR-RV coefficient calibration.

Fetches 7 days of 1-minute OHLCV from Hyperliquid REST and runs OLS.
Run once before paper trading starts, then monthly.

Usage:
  python calibrate_har_rv.py --coins BTC,ETH,SOL --days 7
  python calibrate_har_rv.py --coins BTC --days 14 --save config/har_coefs_BTC.json
"""
import argparse
import logging
import time
from pathlib import Path

import numpy as np
import requests

from econophysics.har_rv import calibrate_har_coefs

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(message)s")

HL_API = "https://api.hyperliquid.xyz/info"
MAX_CANDLES_PER_REQUEST = 500   # Hyperliquid REST limit per call
REQUEST_DELAY_S = 0.5           # Respect rate limits


def fetch_1m_returns(symbol: str, days: int) -> np.ndarray:
    """
    Fetch `days` × 1440 one-minute log-returns for `symbol`.
    Hyperliquid REST is queried in 500-candle chunks with delays.
    Returns 1D numpy array of log-returns.
    """
    total_candles = days * 1440
    end_ms   = int(time.time() * 1000)
    start_ms = end_ms - days * 86_400_000

    all_candles = []
    chunk_start = start_ms

    while chunk_start < end_ms:
        chunk_end = min(chunk_start + MAX_CANDLES_PER_REQUEST * 60_000, end_ms)
        try:
            r = requests.post(
                HL_API,
                json={
                    "type": "candleSnapshot",
                    "req": {
                        "coin": symbol,
                        "interval": "1m",
                        "startTime": chunk_start,
                        "endTime": chunk_end,
                    },
                },
                timeout=15,
            )
            r.raise_for_status()
            data = r.json()
            if data:
                all_candles.extend(data)
            log.debug("Fetched %d candles for %s (chunk %d → %d)",
                      len(data) if data else 0, symbol, chunk_start, chunk_end)
        except Exception as e:
            log.warning("Fetch error for %s: %s — skipping chunk", symbol, e)

        chunk_start = chunk_end
        time.sleep(REQUEST_DELAY_S)

    if not all_candles:
        raise RuntimeError(f"No candle data fetched for {symbol}")

    closes = np.array([float(c["c"]) for c in all_candles])
    closes = closes[closes > 0]

    if len(closes) < 2:
        raise RuntimeError(f"Insufficient candle data for {symbol}")

    log_returns = np.diff(np.log(closes))
    log.info("Fetched %d 1m candles → %d returns for %s",
             len(all_candles), len(log_returns), symbol)
    return log_returns


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--coins", default="BTC,ETH,SOL",
                        help="Comma-separated symbols")
    parser.add_argument("--days",  type=int, default=7,
                        help="Lookback in days (≥7 recommended)")
    parser.add_argument("--save",  default="config/har_coefs.json",
                        help="Output path for coefficients JSON")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.coins.split(",")]

    all_returns = []
    for sym in symbols:
        log.info("Fetching %d days of 1m data for %s...", args.days, sym)
        try:
            rets = fetch_1m_returns(sym, args.days)
            all_returns.append(rets)
        except Exception as e:
            log.error("Failed for %s: %s", sym, e)

    if not all_returns:
        log.error("No data fetched. Abort.")
        return

    combined = np.concatenate(all_returns)
    log.info("Combined dataset: %d returns across %d coins", len(combined), len(all_returns))

    try:
        coefs = calibrate_har_coefs(combined, save_path=args.save)
        log.info("Calibration result:")
        for k, v in coefs.items():
            log.info("  %s = %.4f", k, v)

        if coefs.get("r2", 0) < 0.15:
            log.warning("R² = %.3f is very low. HAR may not fit well on this data. "
                        "Using defaults might be safer.", coefs["r2"])
        elif coefs.get("r2", 0) < 0.30:
            log.warning("R² = %.3f is below target 0.30. "
                        "Consider fetching more days or different coins.", coefs["r2"])
        else:
            log.info("R² = %.3f ✓ Coefficients saved to %s", coefs["r2"], args.save)

    except Exception as e:
        log.error("Calibration failed: %s", e)


if __name__ == "__main__":
    main()
