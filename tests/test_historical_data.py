"""
test_historical_data.py — PHASE 0 : intégrité données, agrégation, loader.

Les tests déterministes (quality_check, aggregate) utilisent des données
synthétiques. Les tests de chargement utilisent le cache parquet s'il existe
(sinon skip — pas de dépendance réseau en CI).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from data import historical_data as H
from backtesting import data_loader


def _synthetic(interval="1h", n=50, start_ms=1_699_992_000_000):  # aligné grille 4h
    ms = H.INTERVAL_MS[interval]
    rows = []
    px = 100.0
    for i in range(n):
        o = px
        c = px * (1.0 + 0.001 * ((-1) ** i))
        rows.append({"ts": start_ms + i * ms, "open": o,
                     "high": max(o, c) * 1.002, "low": min(o, c) * 0.998,
                     "close": c, "volume": 1000.0, "trades": 10,
                     "symbol": "TEST", "interval": interval})
        px = c
    return pd.DataFrame(rows)


def test_quality_clean():
    df = _synthetic("1h", 50)
    q = H.quality_check(df, "1h")
    assert q["n_bars"] == 50
    assert q["n_gaps"] == 0 and q["duplicates"] == 0 and q["bad_prices"] == 0
    assert q["ok"] is True
    assert q["coverage_pct"] == pytest.approx(100.0, abs=0.5)


def test_quality_detects_gap_dup_badprice():
    df = _synthetic("1h", 20)
    ms = H.INTERVAL_MS["1h"]
    # crée un gap (supprime 3 barres au milieu)
    df = pd.concat([df.iloc[:8], df.iloc[11:]], ignore_index=True)
    q = H.quality_check(df, "1h")
    assert q["n_gaps"] >= 1
    assert q["missing_bars"] == 3
    # doublon + prix<=0
    bad = df.copy()
    bad = pd.concat([bad, bad.iloc[[0]]], ignore_index=True)   # doublon ts
    bad.loc[1, "close"] = 0.0                                   # prix<=0
    q2 = H.quality_check(bad, "1h")
    assert q2["duplicates"] >= 1
    assert q2["bad_prices"] >= 1
    assert q2["ok"] is False


def test_aggregate_1h_to_4h():
    df = _synthetic("1h", 8)   # 8 barres 1h → 2 barres 4h
    agg = H.aggregate_ohlcv(df, 4, "4h")
    assert len(agg) == 2
    # OHLC de la 1ère barre 4h = open[0], max(high[0..3]), min(low[0..3]), close[3]
    first4 = df.iloc[:4]
    a0 = agg.iloc[0]
    assert a0["open"] == pytest.approx(first4["open"].iloc[0])
    assert a0["high"] == pytest.approx(first4["high"].max())
    assert a0["low"] == pytest.approx(first4["low"].min())
    assert a0["close"] == pytest.approx(first4["close"].iloc[-1])
    assert a0["volume"] == pytest.approx(first4["volume"].sum())


def test_aggregate_preserves_no_lookahead_alignment():
    """Le bucket d'agrégation est aligné sur l'ouverture : une barre 4h ne
    contient que des barres 1h de SON intervalle (pas de fuite future)."""
    df = _synthetic("1h", 8)
    agg = H.aggregate_ohlcv(df, 4, "4h")
    bucket_ms = H.INTERVAL_MS["1h"] * 4
    for _, a in agg.iterrows():
        members = df[(df["ts"] >= a["ts"]) & (df["ts"] < a["ts"] + bucket_ms)]
        assert a["high"] == pytest.approx(members["high"].max())


@pytest.mark.skipif(not data_loader.historical_path("BTC", "1h").exists(),
                    reason="cache parquet absent (lance python -m data.historical_data)")
def test_loader_returns_ordered_bars():
    bars = data_loader.load_historical_bars("BTC", "1h")
    assert len(bars) > 100
    ts = [b.ts for b in bars]
    assert ts == sorted(ts)                       # ordre temporel strict
    assert all(b.high >= b.low > 0 for b in bars)  # OHLC sain
    assert all(b.high >= b.close and b.close >= b.low for b in bars)


@pytest.mark.skipif(not (Path(data_loader._HIST_DIR) / "BTC_funding.parquet").exists(),
                    reason="cache funding absent")
def test_funding_series_loads_sorted():
    fs = data_loader.load_funding_series("BTC")
    assert len(fs) > 0
    times = [t for t, _ in fs]
    assert times == sorted(times)
