"""Strictly-temporal walk-forward splitting with purge + embargo (§18).

No shuffling ever. Each fold yields index arrays (train / validation / test) built
from the *timestamps*, with:
  - purge:   training rows whose label horizon overlaps the test window are dropped
  - embargo: a gap after the test window is excluded from the next train

This is the single most important defence against look-ahead leakage, so it is a
pure, independently-testable function of (timestamps, config, label_horizon).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional
import numpy as np

from .config import WalkForwardConfig

_DAY = 86400.0


@dataclass
class Fold:
    train: np.ndarray
    validation: np.ndarray
    test: np.ndarray
    train_range: tuple
    test_range: tuple


def make_folds(timestamps: np.ndarray, cfg: WalkForwardConfig,
               label_horizon_seconds: int = 0) -> List[Fold]:
    ts = np.asarray(timestamps, float)
    if len(ts) < 10:
        return []
    t0, t1 = ts[0], ts[-1]
    train_s = cfg.train_days * _DAY
    val_s = cfg.validation_days * _DAY
    test_s = cfg.test_days * _DAY
    step_s = cfg.step_days * _DAY
    purge = cfg.purge_seconds + label_horizon_seconds
    embargo = cfg.embargo_seconds

    folds: List[Fold] = []
    # test window start marches forward by step
    test_start = t0 + train_s + val_s
    while test_start + test_s <= t1 + 1e-6:
        test_end = test_start + test_s
        val_start = test_start - val_s
        train_end = val_start
        if cfg.scheme == "expanding":
            train_start = t0
        else:                       # rolling / walk_forward / fixed
            train_start = train_end - train_s
            if cfg.scheme == "fixed":
                train_start = t0
        train_start = max(train_start, t0)

        train = (ts >= train_start) & (ts < train_end)
        # purge training rows whose label reaches into [val_start, test_end]
        train &= ~((ts + label_horizon_seconds >= val_start - purge) & (ts < val_start))
        val = (ts >= val_start) & (ts < test_start)
        test = (ts >= test_start) & (ts < test_end)
        # embargo: drop the first `embargo` seconds of test from *next* train
        # (handled implicitly because next train_end <= this test_start; we also
        #  exclude embargo region right after test from any future expanding train)
        if train.sum() > 0 and test.sum() > 0:
            folds.append(Fold(
                train=np.where(train)[0], validation=np.where(val)[0],
                test=np.where(test)[0],
                train_range=(float(train_start), float(train_end)),
                test_range=(float(test_start), float(test_end)),
            ))
        test_start += step_s

    # apply embargo across folds: remove from each train any index within `embargo`
    # seconds *after* a prior test window (belt-and-braces against leakage)
    for i, f in enumerate(folds):
        for j in range(i):
            te = folds[j].test_range[1]
            keep = ~((ts[f.train] >= te) & (ts[f.train] < te + embargo))
            f.train = f.train[keep]
    return folds


def single_split(timestamps: np.ndarray, train_frac: float = 0.6, val_frac: float = 0.2,
                 label_horizon_seconds: int = 0, purge_seconds: int = 0) -> Fold:
    """Convenience fixed train/val/test split by fraction of the timeline."""
    ts = np.asarray(timestamps, float)
    n = len(ts)
    i_tr = int(n * train_frac)
    i_val = int(n * (train_frac + val_frac))
    train = np.arange(0, i_tr)
    # purge tail of train that overlaps val label horizon
    if label_horizon_seconds or purge_seconds:
        cutoff = ts[i_tr] - (label_horizon_seconds + purge_seconds)
        train = train[ts[train] < cutoff]
    val = np.arange(i_tr, i_val)
    test = np.arange(i_val, n)
    return Fold(train=train, validation=val, test=test,
                train_range=(float(ts[0]), float(ts[i_tr - 1]) if i_tr else float(ts[0])),
                test_range=(float(ts[i_val]) if i_val < n else float(ts[-1]), float(ts[-1])))
