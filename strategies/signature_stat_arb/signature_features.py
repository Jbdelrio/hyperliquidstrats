"""Truncated path signatures and Lévy areas (§8, §9).

The signature of a path X:[0,T]->R^d truncated at level N is the collection of
iterated integrals up to order N. For discrete data we use the standard
piecewise-linear interpolation through the sampled points. That signature can be
computed exactly by folding per-segment signatures with **Chen's identity**
(tensor product in the truncated tensor algebra). This is what ``iisignature``
does; the pure-numpy fallback here reproduces it for depths 1, 2 and 3 so the
project never *requires* the external library.

Vector layout (matches ``SignatureConfig.dimension``): the returned feature
vector INCLUDES the level-0 constant term (=1), so its length is
``sum_{k=0}^{N} d^k``. ``iisignature`` omits that constant, so when it is used we
prepend the 1 ourselves — the two paths then agree (checked in the tests).

Nothing here looks ahead: the signature at time t is a function of the path up to
t only.
"""
from __future__ import annotations

from typing import List, Optional, Tuple
import numpy as np

try:                                # optional acceleration; fallback is exact too
    import iisignature as _iis      # type: ignore
    _HAS_IIS = True
except Exception:                   # pragma: no cover - environment dependent
    _iis = None
    _HAS_IIS = False


# --------------------------------------------------------------------------- #
#  Single-segment signature and Chen product (numpy fallback)
# --------------------------------------------------------------------------- #
def _segment_signature(delta: np.ndarray, depth: int) -> List[np.ndarray]:
    """Signature of one straight segment with increment ``delta`` (R^d).

    For a linear segment the signature is exp(delta) in the tensor algebra:
    level k = delta^{⊗k} / k!.
    Returns [level1, level2, ...] (level0 is implicitly 1).
    """
    out: List[np.ndarray] = []
    term = np.ones(())           # scalar 1 (the k=0 building block)
    fact = 1.0
    cur = delta
    for k in range(1, depth + 1):
        fact *= k
        if k == 1:
            out.append(delta.astype(float))
        else:
            cur = np.multiply.outer(cur, delta)
            out.append(cur / fact)
    return out


def _chen(A: List[np.ndarray], B: List[np.ndarray], depth: int) -> List[np.ndarray]:
    """Chen product of two signatures (each [lvl1..lvlN], level0=1 implicit).

    C[m] = sum_{k=0}^{m} A[k] ⊗ B[m-k], with A[0]=B[0]=scalar 1 and ⊗ the outer
    product keeping A's indices first.
    """
    def get(S, k):               # k in 1..depth -> tensor ; k==0 -> scalar 1
        return np.ones(()) if k == 0 else S[k - 1]

    C: List[np.ndarray] = []
    for m in range(1, depth + 1):
        acc = None
        for k in range(0, m + 1):
            a = get(A, k)
            b = get(B, m - k)
            term = np.multiply.outer(a, b)
            term = np.asarray(term)
            acc = term if acc is None else acc + term
        C.append(acc)
    return C


def _signature_levels_fallback(path: np.ndarray, depth: int) -> List[np.ndarray]:
    """Fold per-segment signatures over the whole (piecewise-linear) path."""
    n, d = path.shape
    if n < 2:
        return [np.zeros((d,) * k) for k in range(1, depth + 1)]
    deltas = np.diff(path, axis=0)
    sig = _segment_signature(deltas[0], depth)
    for i in range(1, len(deltas)):
        seg = _segment_signature(deltas[i], depth)
        sig = _chen(sig, seg, depth)
    return sig


# --------------------------------------------------------------------------- #
#  Public API
# --------------------------------------------------------------------------- #
def signature(path: np.ndarray, depth: int, use_iisignature: bool = True) -> np.ndarray:
    """Flattened truncated signature INCLUDING the leading level-0 constant.

    ``path`` is (n_points, d). Returns a 1-D vector of length sum_{k=0}^N d^k.
    """
    path = np.asarray(path, dtype=float)
    if path.ndim != 2:
        raise ValueError("path must be 2-D (n_points, d)")
    d = path.shape[1]
    if use_iisignature and _HAS_IIS and path.shape[0] >= 2 and depth >= 1:
        core = _iis.sig(path, depth)                 # length sum_{k=1}^N d^k
        return np.concatenate([[1.0], np.asarray(core, dtype=float)])
    levels = _signature_levels_fallback(path, depth)
    flat = [np.ones(1)]
    for lv in levels:
        flat.append(np.asarray(lv, dtype=float).reshape(-1))
    return np.concatenate(flat)


def signature_dimension(d: int, depth: int) -> int:
    return sum(d ** k for k in range(depth + 1))


def signature_feature_names(channels: List[str], depth: int) -> List[str]:
    """Human-readable names aligned with :func:`signature` output order."""
    short = [_abbrev(c) for c in channels]
    names = ["1"]                                    # level 0
    d = len(channels)
    for k in range(1, depth + 1):
        for idx in np.ndindex(*([d] * k)):
            names.append("S[" + ",".join(short[i] for i in idx) + "]")
    return names


def levy_area(path: np.ndarray, use_iisignature: bool = True) -> np.ndarray:
    """Antisymmetric part of the level-2 signature: A_ij = S_ij - S_ji (d x d).

    A is antisymmetric (A = -A^T); A_ij encodes the signed area / lead-lag
    ordering between channels i and j. It is NOT a causality statement.
    """
    path = np.asarray(path, dtype=float)
    d = path.shape[1]
    if path.shape[0] < 2:
        return np.zeros((d, d))
    if use_iisignature and _HAS_IIS:
        core = np.asarray(_iis.sig(path, 2), dtype=float)
        lvl2 = core[d:d + d * d].reshape(d, d)
    else:
        lvl2 = _signature_levels_fallback(path, 2)[1]
    return lvl2 - lvl2.T


def _abbrev(channel: str) -> str:
    table = {
        "normalized_time": "tau", "spread": "s", "zscore": "z",
        "asset_1_return": "r1", "asset_2_return": "r2", "btc_return": "rBTC",
        "order_flow_imbalance": "ofi", "realized_volatility": "vol",
        "book_spread": "bk", "funding": "fnd",
    }
    return table.get(channel, channel[:4])


# --------------------------------------------------------------------------- #
#  Incremental accumulator (§8: avoid recomputing the whole path each event)
# --------------------------------------------------------------------------- #
class IncrementalSignature:
    """Maintain the signature of a growing path by appending one point at a time.

    Uses Chen's identity: Sig(0, t+dt) = Sig(0, t) ⊗ Sig(segment). Always exact
    (equal to recomputing from scratch, checked in tests).
    """

    def __init__(self, depth: int, d: int):
        self.depth = int(depth)
        self.d = int(d)
        self._levels: Optional[List[np.ndarray]] = None
        self._last: Optional[np.ndarray] = None
        self.n_points = 0

    def append(self, point: np.ndarray):
        point = np.asarray(point, dtype=float).reshape(-1)
        if point.shape[0] != self.d:
            raise ValueError("point dimension mismatch")
        self.n_points += 1
        if self._last is None:
            self._last = point
            return
        seg = _segment_signature(point - self._last, self.depth)
        self._levels = seg if self._levels is None else _chen(self._levels, seg, self.depth)
        self._last = point

    def value(self) -> np.ndarray:
        if self._levels is None:
            return np.concatenate([[1.0]] + [np.zeros(self.d ** k) for k in range(1, self.depth + 1)])
        flat = [np.ones(1)] + [lv.reshape(-1) for lv in self._levels]
        return np.concatenate(flat)


def rolling_signatures(paths_channels: np.ndarray, window: int, depth: int,
                       use_iisignature: bool = True) -> np.ndarray:
    """Signature over a trailing window ending at each row (causal).

    ``paths_channels`` is (T, d). Row t uses rows (t-window+1 .. t). Rows before a
    full window get the signature of whatever prefix exists (>=1 point).
    Returns (T, m).
    """
    T, d = paths_channels.shape
    m = signature_dimension(d, depth)
    out = np.zeros((T, m))
    for t in range(T):
        lo = max(0, t - window + 1)
        seg = paths_channels[lo:t + 1]
        out[t] = signature(seg, depth, use_iisignature=use_iisignature)
    return out
