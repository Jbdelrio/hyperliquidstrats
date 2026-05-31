"""
historical_data.py — Couche de données historiques Hyperliquid (PHASE 0).

Constitue un dataset local rejouable du TOP 20 perps HL, en respectant les
limites de rétention de l'API (rétention courte en 1m, plus longue en 15m/1h).

Réutilise les conventions de data/hyperliquid_funding.py (endpoint /info,
metaAndAssetCtxs, fundingHistory). N'introduit pas de nouveau framework : produit
des parquets que backtesting/data_loader.py charge en BarData.

Sorties :
  data/historical/{coin}_{interval}.parquet   (OHLCV)
  data/historical/{coin}_funding.parquet       (funding horaire)
  data/historical/manifest.json                (coin, interval, n_bars, range, confidence)
  reports/data_quality.md                       (gaps, doublons, prix<=0, trous funding)

Usage :
    python -m data.historical_data                 # construit le dataset (cache si présent)
    python -m data.historical_data --refresh        # re-télécharge tout
    python -m data.historical_data --top 20 --intervals 1m,15m,1h
"""
from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

log = logging.getLogger(__name__)

_API_URL = "https://api.hyperliquid.xyz/info"
_TIMEOUT = 20.0
_MAX_BARS_PER_REQ = 5000          # plafond observé de candleSnapshot
_REQ_PAUSE_S = 0.15               # politesse rate-limit
_MAX_RETRIES = 5

ROOT = Path(__file__).resolve().parents[1]
HIST_DIR = ROOT / "data" / "historical"
MANIFEST = HIST_DIR / "manifest.json"
QUALITY_REPORT = ROOT / "reports" / "data_quality.md"

# Intervalle -> millisecondes
INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}

# Budgets de lookback (jours) + niveau de confiance PAR intervalle natif.
# On prend ce que l'API donne, plafonné à ce budget.
LOOKBACK_DAYS = {"1m": 5, "15m": 60, "1h": 180}
CONFIDENCE = {"1m": "LOW", "15m": "MEDIUM", "1h": "HIGH",
              "4h": "HIGH", "1d": "MEDIUM"}   # 4h/1d dérivés du 1h (span limité)

# Agrégations dérivées localement depuis le 1h.
DERIVED_FROM_1H = {"4h": 4, "1d": 24}


# ─────────────────────────── TOP 20 dynamique ───────────────────────────────

def top_coins(n: int = 20) -> list[dict]:
    """Classe les perps par volume notionnel 24h (dayNtlVlm), garde les n premiers.
    Retourne [{name, vol24, max_leverage, oracle_px, mark_px}]. Jamais hardcodé."""
    resp = requests.post(_API_URL, json={"type": "metaAndAssetCtxs"}, timeout=_TIMEOUT)
    data = resp.json()
    universe, ctxs = data[0]["universe"], data[1]
    rows = []
    for i, a in enumerate(universe):
        c = ctxs[i] if i < len(ctxs) else {}
        rows.append({
            "name": a["name"],
            "vol24": float(c.get("dayNtlVlm", 0) or 0),
            "max_leverage": int(a.get("maxLeverage", 0) or 0),
            "oracle_px": float(c.get("oraclePx", 0) or 0),
            "mark_px": float(c.get("markPx", 0) or 0),
        })
    rows.sort(key=lambda r: -r["vol24"])
    return rows[:n]


# ─────────────────────────── candleSnapshot paginé ──────────────────────────

def _post(payload: dict) -> Optional[list]:
    """POST /info avec backoff exponentiel sur 429/erreur."""
    for attempt in range(_MAX_RETRIES):
        try:
            r = requests.post(_API_URL, json=payload, timeout=_TIMEOUT)
            if r.status_code == 429:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if attempt == _MAX_RETRIES - 1:
                log.warning("POST %s échec définitif: %s", payload.get("type"), e)
                return None
            time.sleep(0.8 * (attempt + 1))
    return None


def fetch_candles_paginated(coin: str, interval: str,
                            lookback_days: Optional[float] = None) -> pd.DataFrame:
    """
    Remonte dans le temps en ajustant endTime jusqu'à ce que l'API cesse de
    renvoyer des bougies plus anciennes (= mur de rétention) ou que le budget
    lookback soit atteint. Renvoie un DataFrame OHLCV trié, dédupliqué.
    """
    ms = INTERVAL_MS[interval]
    if lookback_days is None:
        lookback_days = LOOKBACK_DAYS.get(interval, 5)
    now = int(time.time() * 1000)
    target_start = now - int(lookback_days * 86_400_000)

    chunks: list[list] = []
    cursor_end = now
    safety = 0
    while cursor_end > target_start and safety < 200:
        safety += 1
        start = max(target_start, cursor_end - _MAX_BARS_PER_REQ * ms)
        data = _post({"type": "candleSnapshot", "req": {
            "coin": coin, "interval": interval,
            "startTime": start, "endTime": cursor_end}})
        if not data:
            break                                  # mur de rétention / vide
        chunks.append(data)
        oldest = min(int(c["t"]) for c in data)
        if oldest <= target_start or oldest >= cursor_end:
            break                                  # plus rien de plus ancien
        cursor_end = oldest - 1
        time.sleep(_REQ_PAUSE_S)

    if not chunks:
        return pd.DataFrame()
    flat = [c for ch in chunks for c in ch]
    df = pd.DataFrame(flat)
    for col in ("o", "h", "l", "c", "v"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["n"] = pd.to_numeric(df.get("n", 0), errors="coerce").fillna(0)
    df = df.rename(columns={"t": "ts", "o": "open", "h": "high",
                            "l": "low", "c": "close", "v": "volume", "n": "trades"})
    df = df[["ts", "open", "high", "low", "close", "volume", "trades"]]
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce").astype("int64")
    df = (df.dropna(subset=["close"])
            .drop_duplicates(subset=["ts"], keep="last")
            .sort_values("ts").reset_index(drop=True))
    df["symbol"] = coin
    df["interval"] = interval
    return df


def fetch_funding_history(coin: str, start_ms: int, end_ms: Optional[int] = None) -> pd.DataFrame:
    """fundingHistory paginé en avant. Colonnes: time(ms), funding_rate, premium."""
    if end_ms is None:
        end_ms = int(time.time() * 1000)
    rows: list[dict] = []
    cursor = start_ms
    safety = 0
    while cursor < end_ms and safety < 200:
        safety += 1
        data = _post({"type": "fundingHistory", "coin": coin,
                      "startTime": cursor, "endTime": end_ms})
        if not data:
            break
        rows.extend(data)
        newest = max(int(r["time"]) for r in data)
        if newest <= cursor:
            break
        cursor = newest + 1
        time.sleep(_REQ_PAUSE_S)
    if not rows:
        return pd.DataFrame(columns=["time", "funding_rate", "premium"])
    df = pd.DataFrame(rows)
    df["time"] = pd.to_numeric(df["time"], errors="coerce").astype("int64")
    df["funding_rate"] = pd.to_numeric(df.get("fundingRate"), errors="coerce")
    df["premium"] = pd.to_numeric(df.get("premium"), errors="coerce")
    df = (df[["time", "funding_rate", "premium"]]
          .dropna(subset=["funding_rate"])
          .drop_duplicates(subset=["time"], keep="last")
          .sort_values("time").reset_index(drop=True))
    return df


# ─────────────────────────── agrégation locale ──────────────────────────────

def aggregate_ohlcv(df: pd.DataFrame, factor: int, interval_label: str) -> pd.DataFrame:
    """Agrège des barres en barres `factor`× plus longues (ex. 1h→4h: factor=4).
    Bucket aligné sur le timestamp d'ouverture. OHLC standard, volume sommé."""
    if df.empty:
        return df
    base_ms = INTERVAL_MS[df["interval"].iloc[0]]
    bucket_ms = base_ms * factor
    g = df.copy()
    g["bucket"] = (g["ts"] // bucket_ms) * bucket_ms
    agg = g.groupby("bucket").agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"), trades=("trades", "sum"),
    ).reset_index().rename(columns={"bucket": "ts"})
    agg["symbol"] = df["symbol"].iloc[0]
    agg["interval"] = interval_label
    return agg[["ts", "open", "high", "low", "close", "volume", "trades", "symbol", "interval"]]


# ─────────────────────────── contrôle qualité ───────────────────────────────

def quality_check(df: pd.DataFrame, interval: str) -> dict:
    """Gaps temporels, doublons, prix<=0. Renvoie un dict de métriques qualité."""
    if df.empty:
        return {"n_bars": 0, "ok": False, "reason": "no data"}
    ms = INTERVAL_MS[interval]
    ts = df["ts"].to_numpy()
    diffs = ts[1:] - ts[:-1]
    # nombre de barres manquantes (gaps) = somme des (écart/ms - 1) là où écart > ms
    missing = int(sum((d // ms - 1) for d in diffs if d > ms))
    n_gaps = int((diffs > ms).sum())
    dups = int(df["ts"].duplicated().sum())
    bad_px = int(((df[["open", "high", "low", "close"]] <= 0).any(axis=1)).sum())
    span_h = (ts[-1] - ts[0]) / 3_600_000.0
    expected = int(span_h * 3_600_000 / ms) + 1
    coverage = len(df) / expected if expected else 0.0
    return {
        "n_bars": len(df),
        "start": pd.to_datetime(int(ts[0]), unit="ms", utc=True).isoformat(),
        "end": pd.to_datetime(int(ts[-1]), unit="ms", utc=True).isoformat(),
        "span_days": round(span_h / 24.0, 2),
        "n_gaps": n_gaps, "missing_bars": missing,
        "duplicates": dups, "bad_prices": bad_px,
        "coverage_pct": round(coverage * 100, 1),
        "ok": bad_px == 0 and dups == 0,
    }


# ─────────────────────────── orchestration ──────────────────────────────────

def build_dataset(top_n: int = 20, intervals: Optional[list] = None,
                  refresh: bool = False) -> dict:
    intervals = intervals or ["1m", "15m", "1h"]
    HIST_DIR.mkdir(parents=True, exist_ok=True)
    QUALITY_REPORT.parent.mkdir(parents=True, exist_ok=True)

    coins = top_coins(top_n)
    names = [c["name"] for c in coins]
    print(f"TOP {len(names)} (par volume 24h): {', '.join(names)}")

    manifest: dict = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                      "top_coins": coins, "data": {}}
    quality_rows: list[dict] = []

    for coin in names:
        manifest["data"].setdefault(coin, {})
        # ── intervalles natifs ──
        native: dict[str, pd.DataFrame] = {}
        for itv in intervals:
            path = HIST_DIR / f"{coin}_{itv}.parquet"
            if path.exists() and not refresh:
                df = pd.read_parquet(path)
            else:
                df = fetch_candles_paginated(coin, itv)
                if not df.empty:
                    df.to_parquet(path)
            native[itv] = df
            q = quality_check(df, itv)
            manifest["data"][coin][itv] = {
                "n_bars": q.get("n_bars", 0), "start": q.get("start"),
                "end": q.get("end"), "span_days": q.get("span_days"),
                "confidence": CONFIDENCE.get(itv, "LOW"), "coverage_pct": q.get("coverage_pct"),
            }
            quality_rows.append({"coin": coin, "interval": itv, **q,
                                 "confidence": CONFIDENCE.get(itv, "LOW")})
            print(f"  {coin:7s} {itv:3s}: {q.get('n_bars',0):6d} bars "
                  f"({q.get('span_days','?')}j) gaps={q.get('n_gaps','?')} "
                  f"dups={q.get('duplicates','?')} conf={CONFIDENCE.get(itv)}")

        # ── dérivés 4h / 1d depuis le 1h ──
        if "1h" in native and not native["1h"].empty:
            for itv, factor in DERIVED_FROM_1H.items():
                dfa = aggregate_ohlcv(native["1h"], factor, itv)
                if not dfa.empty:
                    dfa.to_parquet(HIST_DIR / f"{coin}_{itv}.parquet")
                    q = quality_check(dfa, itv)
                    manifest["data"][coin][itv] = {
                        "n_bars": q.get("n_bars", 0), "start": q.get("start"),
                        "end": q.get("end"), "span_days": q.get("span_days"),
                        "confidence": CONFIDENCE.get(itv), "derived_from": "1h",
                        "coverage_pct": q.get("coverage_pct"),
                    }
                    quality_rows.append({"coin": coin, "interval": itv, **q,
                                         "confidence": CONFIDENCE.get(itv), "derived": True})

        # ── funding (fenêtre du 1h) ──
        fpath = HIST_DIR / f"{coin}_funding.parquet"
        if fpath.exists() and not refresh:
            fdf = pd.read_parquet(fpath)
        else:
            start_ms = int(time.time() * 1000) - int(LOOKBACK_DAYS["1h"] * 86_400_000)
            fdf = fetch_funding_history(coin, start_ms)
            if not fdf.empty:
                fdf.to_parquet(fpath)
        manifest["data"][coin]["funding"] = {
            "n_points": int(len(fdf)),
            "start": pd.to_datetime(int(fdf["time"].iloc[0]), unit="ms", utc=True).isoformat() if len(fdf) else None,
            "end": pd.to_datetime(int(fdf["time"].iloc[-1]), unit="ms", utc=True).isoformat() if len(fdf) else None,
        }

    MANIFEST.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    _write_quality_report(quality_rows, coins)
    print(f"\nManifest -> {MANIFEST}")
    print(f"Qualité  -> {QUALITY_REPORT}")
    return manifest


def _write_quality_report(rows: list[dict], coins: list[dict]) -> None:
    lines = ["# Qualité des données historiques Hyperliquid\n",
             f"*{time.strftime('%Y-%m-%dT%H:%M:%S')} · TOP {len(coins)} par volume 24h*\n",
             "Budgets : 1m≈5j (LOW) · 15m≈60j (MEDIUM) · 1h≈180j (HIGH). "
             "4h/1d **dérivés du 1h** (span borné par celui du 1h).\n",
             "**Limite connue** : l'oracle/index historique n'est PAS exposé par "
             "l'API publique HL (seulement le snapshot courant via metaAndAssetCtxs) "
             "→ MarkOracleDislocation (Phase 5) ne pourra pas être backtestée sur "
             "historique, seulement observée en live.\n",
             "| Coin | Itv | Conf | Barres | Span (j) | Couverture | Gaps | Manquantes | Dups | Prix≤0 |",
             "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        lines.append(
            f"| {r['coin']} | {r['interval']} | {r.get('confidence','?')} | "
            f"{r.get('n_bars',0)} | {r.get('span_days','?')} | {r.get('coverage_pct','?')}% | "
            f"{r.get('n_gaps','?')} | {r.get('missing_bars','?')} | "
            f"{r.get('duplicates','?')} | {r.get('bad_prices','?')} |")
    QUALITY_REPORT.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    try:
        import sys
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    logging.basicConfig(level=logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--intervals", default="1m,15m,1h")
    ap.add_argument("--refresh", action="store_true")
    args = ap.parse_args()
    intervals = [s.strip() for s in args.intervals.split(",") if s.strip()]
    build_dataset(args.top, intervals, args.refresh)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
