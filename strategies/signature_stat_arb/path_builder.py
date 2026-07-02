"""Information-path construction Z_t and leak-free normalization (§7).

Assembles the (T, d) channel matrix from raw market series, in the exact order of
``SignatureConfig.channels`` (so it lines up with the signature term names).

Normalization never leaks the future:
    rolling_*   : trailing-window statistics (causal by construction)
    train_fit   : statistics fit ONLY on the training slice, applied everywhere
    none        : raw values
"""
from __future__ import annotations

from typing import Dict, Optional
import numpy as np
import pandas as pd

from .config import SignatureConfig


# channels that are unavailable in a given dataset are filled with zeros and the
# name is reported so the UI/logs can show which were inactive.
def build_channel_matrix(cfg: SignatureConfig, series: Dict[str, np.ndarray],
                         window_bars: int) -> "PathMatrix":
    T = len(series["spread"])
    cols = []
    missing = []
    for ch in cfg.channels:
        if ch == "normalized_time":
            # increments of 1/window so any trailing window spans tau in [0,1]
            v = np.arange(T, dtype=float) / max(1, window_bars)
        elif ch in series and series[ch] is not None:
            v = np.asarray(series[ch], float)
        else:
            v = np.zeros(T)
            missing.append(ch)
        cols.append(v)
    mat = np.column_stack(cols) if cols else np.zeros((T, 0))
    return PathMatrix(matrix=mat, channels=list(cfg.channels), missing=missing)


class PathMatrix:
    def __init__(self, matrix: np.ndarray, channels, missing):
        self.matrix = matrix
        self.channels = channels
        self.missing = missing


class Normalizer:
    """Per-channel normalization with an explicit train/test split guarantee.

    ``normalized_time`` is never rescaled (it must stay a clean time axis).
    """

    def __init__(self, cfg: SignatureConfig, window_bars: int):
        self.cfg = cfg
        self.window = window_bars
        self._mean: Optional[np.ndarray] = None
        self._scale: Optional[np.ndarray] = None
        self._fitted = False

    def fit(self, matrix: np.ndarray, train_mask: Optional[np.ndarray] = None):
        """Only used by normalization='train_fit'. Stats from the train rows only."""
        if train_mask is None:
            sub = matrix
        else:
            sub = matrix[train_mask]
        self._mean = np.nanmean(sub, axis=0)
        self._scale = np.nanstd(sub, axis=0)
        self._scale[self._scale == 0] = 1.0
        self._fitted = True
        return self

    def transform(self, pm: PathMatrix) -> np.ndarray:
        mat = pm.matrix.astype(float)
        mode = self.cfg.normalization
        time_idx = pm.channels.index("normalized_time") if "normalized_time" in pm.channels else -1
        if mode == "none":
            return mat
        out = np.empty_like(mat)
        for j in range(mat.shape[1]):
            if j == time_idx:
                out[:, j] = mat[:, j]           # keep the time axis raw
                continue
            col = mat[:, j]
            if mode == "rolling_zscore":
                out[:, j] = self._roll_z(col)
            elif mode == "rolling_robust":
                out[:, j] = self._roll_robust(col)
            elif mode == "rolling_minmax":
                out[:, j] = self._roll_minmax(col)
            elif mode == "train_fit":
                if not self._fitted:
                    raise RuntimeError("train_fit normalization requires .fit() first")
                out[:, j] = (col - self._mean[j]) / self._scale[j]
            else:                                # pragma: no cover
                out[:, j] = col
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)

    # ---- causal rolling transforms ---------------------------------------
    def _roll_z(self, col):
        s = pd.Series(col); mp = max(5, self.window // 10)
        mu = s.rolling(self.window, min_periods=mp).mean()
        sd = s.rolling(self.window, min_periods=mp).std(ddof=0).replace(0, np.nan)
        return ((s - mu) / sd).to_numpy()

    def _roll_robust(self, col):
        s = pd.Series(col); mp = max(5, self.window // 10)
        med = s.rolling(self.window, min_periods=mp).median()
        q75 = s.rolling(self.window, min_periods=mp).quantile(0.75)
        q25 = s.rolling(self.window, min_periods=mp).quantile(0.25)
        iqr = (q75 - q25).replace(0, np.nan)
        return ((s - med) / iqr).to_numpy()

    def _roll_minmax(self, col):
        s = pd.Series(col); mp = max(5, self.window // 10)
        lo = s.rolling(self.window, min_periods=mp).min()
        hi = s.rolling(self.window, min_periods=mp).max()
        rng = (hi - lo).replace(0, np.nan)
        return ((s - lo) / rng).to_numpy()
