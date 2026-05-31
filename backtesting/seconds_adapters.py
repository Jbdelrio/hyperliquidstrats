"""
backtesting/seconds_adapters.py — adaptateurs run_fn pour les stratégies à
features-secondes (PHASE 3 : B6 AlphaSignalDecile, D1 BTC binary).

Source : data/processed/seconds_features.parquet (~1 Hz, ~4.6 jours, confidence
LOW). On NE modélise pas un carnet complet : le coût dominant à l'échelle seconde
est le SPREAD. Modèle de coût honnête, paramétré par maker/taker :

  taker : on traverse le spread → cost_rt = spread_bps + 2·fee_bps
  maker : entrée passive qui CAPTURE le demi-spread (hypothèse OPTIMISTE de fill,
          pas de probabilité de fill modélisée → à lire comme borne haute),
          sortie taker → cost_rt ≈ 2·fee_bps (le spread se compense)

Seuil de décile : calé sur les premiers 40% (passé) puis appliqué en avant
(causal) ; le harnais walk-forward purge/fold ensuite la partie avant. Zéro
look-ahead : gross = mid[i] → mid[i+H], entrée à i (signal connu à i).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PARQUET = ROOT / "data" / "processed" / "seconds_features.parquet"

_CACHE: dict[str, pd.DataFrame] = {}
_BASE_COLS = ["ts", "symbol", "mid", "spread_bps"]


def _load_coin(coin: str, feature: str) -> pd.DataFrame:
    key = f"{coin}|{feature}"
    if key in _CACHE:
        return _CACHE[key]
    cols = list(dict.fromkeys(_BASE_COLS + [feature]))
    df = pd.read_parquet(PARQUET, columns=cols)
    df = df[df["symbol"] == coin].copy()
    for c in cols:
        if c != "symbol":
            df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["ts", "mid", feature]).sort_values("ts").reset_index(drop=True)
    _CACHE[key] = df
    return df


def decile_run_fn(feature: str, side: str, maker: bool):
    """run_fn(params{horizon_s, decile}, coin, fee_bps, slip_bps) -> trades.
    Réplique B6 : franchissement de décile → position time-stoppée à horizon_s."""
    def run_fn(params: dict, coin: str, fee_bps: float, slip_bps: float) -> list:
        H = int(params["horizon_s"])
        decile = float(params["decile"])
        try:
            g = _load_coin(coin, feature)
        except Exception:
            return []
        n = len(g)
        if n < 5000:
            return []
        mid = g["mid"].to_numpy(float)
        ts = g["ts"].to_numpy(float)
        spread = g["spread_bps"].to_numpy(float)
        feat = g[feature].to_numpy(float)
        split = int(n * 0.4)                      # seuil calé sur le passé (causal)
        if side == "long":
            thr = float(np.nanquantile(feat[:split], 1 - decile))
            cross = feat >= thr
            sgn = 1.0
        else:
            thr = float(np.nanquantile(feat[:split], decile))
            cross = feat <= thr
            sgn = -1.0
        notional = 1000.0
        trades = []
        i = split
        while i < n - H:
            if not cross[i] or mid[i] <= 0:
                i += 1
                continue
            gross_bps = (mid[i + H] - mid[i]) / mid[i] * 1e4 * sgn
            sp = spread[i] if np.isfinite(spread[i]) else 6.0
            cost_rt = (sp + 2 * fee_bps) if not maker else (2 * fee_bps)
            net_bps = gross_bps - cost_rt
            trades.append({"ts": ts[i + H], "hold_s": ts[i + H] - ts[i],
                           "net": notional * net_bps / 1e4, "notional": notional})
            i += H + 1                            # pas de chevauchement
        return trades
    return run_fn


def binary_dir_run_fn(feature: str = "obi_3", side: str = "long",
                      leverage: float = 5.0):
    """run_fn pour D1 (binaire directionnel) : signal microstructure BTC, levier
    ≤5x. L'AvgNet_bps (edge) est invariant au levier sauf liquidation — laquelle
    ne se déclenche jamais à 5x sur BTC en 5 min (liq ~19.5%). Donc le levier ne
    fait que SCALER l'edge ; tout test 100-150x ajouterait seulement des pertes
    de liquidation (interdit). On mesure donc l'edge net directionnel à 5x."""
    return decile_run_fn(feature, side, maker=False)
