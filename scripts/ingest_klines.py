"""
ingest_klines.py — ingère les klines longue-histoire (datafetcher) dans la couche
historique du backtester (PHASE 0). Transforme 1m (jusqu'à 2020) en barres
agrégées 15m/1h/4h/1d au format attendu par backtesting/data_loader.

Source : C:/Users/jeanb/Documents/Mercantour/datafetcher/klines/{exchange}/{COIN}_USDT_1m.parquet
         (colonnes datetime, open, high, low, close, volume) — ~6.4 ans, BTC/ETH/SOL.
Sortie : data/historical/{COIN}_{interval}.parquet (ts ms + OHLCV) → confidence HIGH.

Usage :
    python scripts/ingest_klines.py                       # binance, BTC/ETH/SOL, 15m/1h/4h/1d
    python scripts/ingest_klines.py --exchange okx --coins BTC,ETH
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from data.historical_data import aggregate_ohlcv, quality_check, INTERVAL_MS   # noqa: E402

SRC = Path(r"C:/Users/jeanb/Documents/Mercantour/datafetcher/klines")
HIST = ROOT / "data" / "historical"
DERIVED = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}   # facteur depuis 1m


def ingest(coin: str, exchange: str) -> dict:
    src = SRC / exchange / f"{coin}_USDT_1m.parquet"
    if not src.exists():
        print(f"  SKIP {coin} ({src} introuvable)")
        return {}
    df = pd.read_parquet(src)
    df["ts"] = (pd.to_datetime(df["datetime"], utc=True).astype("int64") // 1_000_000)  # ms
    df = df[["ts", "open", "high", "low", "close", "volume"]].copy()
    df["trades"] = 0
    df["symbol"] = coin
    df["interval"] = "1m"
    df = df.dropna(subset=["close"]).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    HIST.mkdir(parents=True, exist_ok=True)
    out = {}
    for label, factor in DERIVED.items():
        agg = aggregate_ohlcv(df, factor, label)
        if agg.empty:
            continue
        agg.to_parquet(HIST / f"{coin}_{label}.parquet")
        q = quality_check(agg, label)
        out[label] = q
        print(f"  {coin:4s} {label:3s}: {q['n_bars']:6d} barres  {q.get('span_days','?')}j  "
              f"gaps={q.get('n_gaps','?')} dups={q.get('duplicates','?')}  [{exchange}]")
    return out


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--coins", default="BTC,ETH,SOL")
    args = ap.parse_args()
    coins = [c.strip().upper() for c in args.coins.split(",") if c.strip()]
    print(f"Ingestion klines {args.exchange} -> data/historical/ (confidence HIGH, ~6 ans)")
    for c in coins:
        ingest(c, args.exchange)
    print("\nFait. Le harnais et le backtester utilisent maintenant ces longues séries.")
    print("Ex: python scripts/backtest_hourly_breakout.py --coins BTC,ETH,SOL --cost_bps 9")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
