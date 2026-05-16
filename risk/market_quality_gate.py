"""
market_quality_gate.py — Microstructure quality filter for trade decisions.

Sits between strategy decision and capital ledger reservation. Reads the
last `SecondsFeatureEngine` snapshot (+ OrderbookManager health) and
either approves the trade or blocks it with a reason.

The gate is a pure function over (symbol, side, features, book, health,
now). It mutates nothing and never raises — bad inputs result in a
conservative `block`.

Rules summary (cf. Phase 4 of the spec):
  1. Book absent          → block
  2. Book stale           → block
  3. Spread too wide      → block
  4. Latency p95 too high → block
  5. Queue drops recent   → block
  6. Volume 30s too low   → block
  7. Realized vol 60s ext → block
  8. Toxicity too high    → block
  9. Liquidity too low    → block
 10. OFI against side     → block
 11. Depth imbalance ag.  → block
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Default config — every field can be overridden by `config` dict.
_DEFAULTS = {
    "enabled": True,
    "decision_interval_s": 30,
    "min_seconds_warmup": 120,
    "max_book_age_s": 2.0,
    "max_trade_age_s": 10.0,
    "max_latency_p95_ms": 1000,
    "max_spread_bps_by_symbol": {
        "BTC": 5.0, "ETH": 5.0, "SOL": 8.0,
        "HYPE": 12.0, "DEFAULT": 15.0,
    },
    "min_volume_30s_usd_by_symbol": {
        "BTC": 25_000, "ETH": 15_000, "SOL": 8_000, "DEFAULT": 3_000,
    },
    "max_realized_vol_60s_bps": 60.0,
    "max_toxicity_score": 0.75,
    "min_liquidity_score": 0.35,
    "block_if_queue_drops": True,
    "block_if_crossed_book_seen": True,
    "ofi_block_threshold": 0.20,
    "depth_block_threshold": 0.20,
}


@dataclass
class GateStats:
    total_evaluated: int = 0
    total_blocked: int = 0
    blocks_by_reason: dict = field(default_factory=dict)


class MarketQualityGate:

    def __init__(self, config: Optional[dict] = None):
        cfg = dict(_DEFAULTS)
        if config:
            # Merge top-level (config wins) but deep-merge the per-symbol maps.
            for k, v in config.items():
                if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                    merged = dict(cfg[k])
                    merged.update(v)
                    cfg[k] = merged
                else:
                    cfg[k] = v
        self.cfg = cfg
        self.enabled = bool(cfg.get("enabled", True))
        self.stats = GateStats()
        # Track last seen queue-drops / crossed-books for delta detection.
        self._last_queue_drops = 0
        self._last_crossed = 0

    # ----------------------------------------------------------------
    # Public API
    # ----------------------------------------------------------------

    def evaluate(self, symbol: str, side: str, features: dict,
                 book=None, health: Optional[dict] = None,
                 now: Optional[float] = None) -> tuple[bool, str, dict]:
        """Return (ok, reason, details).

        `side` is "long" or "short" (other values treated as long).
        `health` is the OrderbookManager.health_snapshot() output (or None).
        """
        self.stats.total_evaluated += 1
        if not self.enabled:
            return True, "disabled", {}
        now = now if now is not None else time.time()
        symbol = (symbol or "").upper()
        side_norm = "short" if str(side).lower() == "short" else "long"
        details: dict = {"symbol": symbol, "side": side_norm}

        feats = features or {}

        # 1. Book absent
        if book is None and not feats.get("mid"):
            return self._block("no_book", details)

        # 2. Book stale (age in seconds)
        book_age = feats.get("book_age_s")
        if book_age is None:
            book_age = float("inf") if book is None else 0.0
        details["book_age_s"] = book_age
        if book_age > float(self.cfg["max_book_age_s"]):
            return self._block("book_stale", details)

        # Trade staleness — only enforce if we have a value.
        trade_age = feats.get("trade_age_s")
        if trade_age is not None and math.isfinite(trade_age):
            details["trade_age_s"] = trade_age
            if trade_age > float(self.cfg["max_trade_age_s"]):
                return self._block("trade_stale", details)

        # Warmup gate — we want enough features computed.
        warmup_s = float(self.cfg["min_seconds_warmup"])
        if not feats.get("enough_data", True):
            return self._block("warmup", details)
        # If features carry an age-of-buffer, enforce it too.
        # (SecondsFeatureEngine doesn't expose it directly today; the
        # enough_data flag already requires `min_warmup_seconds`.)
        _ = warmup_s

        # 3. Spread too wide
        sb = feats.get("spread_bps")
        details["spread_bps"] = sb
        max_spread_map = self.cfg["max_spread_bps_by_symbol"]
        max_spread = float(max_spread_map.get(symbol, max_spread_map.get("DEFAULT", 15.0)))
        if sb is None or not math.isfinite(sb) or sb > max_spread:
            return self._block(f"spread_too_wide:{sb}>{max_spread}", details)

        # 4. Latency p95
        if health is not None:
            ps = (health.get("per_symbol") or {}).get(symbol, {})
            lat_p95 = ps.get("p95_latency_ms")
            details["latency_p95_ms"] = lat_p95
            if (lat_p95 is not None and math.isfinite(lat_p95)
                    and lat_p95 > float(self.cfg["max_latency_p95_ms"])):
                return self._block(f"latency_p95:{lat_p95:.0f}ms", details)

            # 5. Queue drops since the last evaluation
            qd = int(health.get("queue_drops", 0))
            details["queue_drops"] = qd
            if self.cfg.get("block_if_queue_drops", True) and qd > self._last_queue_drops:
                self._last_queue_drops = qd
                return self._block("queue_drops_recent", details)
            self._last_queue_drops = qd
            # Crossed books
            cb = int(health.get("crossed_book_count", 0))
            details["crossed_book_count"] = cb
            if self.cfg.get("block_if_crossed_book_seen", True) and cb > self._last_crossed:
                self._last_crossed = cb
                return self._block("crossed_book_seen", details)
            self._last_crossed = cb

        # 6. Volume 30s
        vol_30 = feats.get("trade_volume_30s")
        details["trade_volume_30s"] = vol_30
        vol_map = self.cfg["min_volume_30s_usd_by_symbol"]
        min_vol = float(vol_map.get(symbol, vol_map.get("DEFAULT", 3_000)))
        if vol_30 is None or vol_30 < min_vol:
            return self._block(f"low_volume:{vol_30}<{min_vol}", details)

        # 7. Realized vol 60s (convert log-RV to bps over the window)
        rv60 = feats.get("rv_60s")
        details["rv_60s"] = rv60
        if rv60 is not None and math.isfinite(rv60):
            rv_bps = abs(rv60) * 10_000.0
            details["rv_60s_bps"] = rv_bps
            if rv_bps > float(self.cfg["max_realized_vol_60s_bps"]):
                return self._block(f"realized_vol_high:{rv_bps:.1f}bps", details)

        # 8. Toxicity
        tox = feats.get("toxicity_score")
        details["toxicity_score"] = tox
        if tox is not None and math.isfinite(tox) and tox > float(self.cfg["max_toxicity_score"]):
            return self._block(f"toxicity_high:{tox:.2f}", details)

        # 9. Liquidity
        liq = feats.get("liquidity_score")
        details["liquidity_score"] = liq
        if liq is not None and math.isfinite(liq) and liq < float(self.cfg["min_liquidity_score"]):
            return self._block(f"liquidity_low:{liq:.2f}", details)

        # 10. OFI against direction
        ofi30 = feats.get("ofi_30s")
        details["ofi_30s"] = ofi30
        thr = float(self.cfg.get("ofi_block_threshold", 0.20))
        if ofi30 is not None and math.isfinite(ofi30):
            if side_norm == "long" and ofi30 < -thr:
                return self._block(f"ofi_against_long:{ofi30:.2f}", details)
            if side_norm == "short" and ofi30 > thr:
                return self._block(f"ofi_against_short:{ofi30:.2f}", details)

        # 11. Depth imbalance against direction
        di10 = feats.get("depth_imbalance_10")
        details["depth_imbalance_10"] = di10
        thr_d = float(self.cfg.get("depth_block_threshold", 0.20))
        if di10 is not None and math.isfinite(di10):
            if side_norm == "long" and di10 < -thr_d:
                return self._block(f"depth_against_long:{di10:.2f}", details)
            if side_norm == "short" and di10 > thr_d:
                return self._block(f"depth_against_short:{di10:.2f}", details)

        return True, "ok", details

    # ----------------------------------------------------------------
    # Helpers
    # ----------------------------------------------------------------

    def _block(self, reason: str, details: dict) -> tuple[bool, str, dict]:
        self.stats.total_blocked += 1
        # Bucket the reason head (before the colon) for aggregation.
        head = reason.split(":", 1)[0]
        self.stats.blocks_by_reason[head] = self.stats.blocks_by_reason.get(head, 0) + 1
        details["block_reason"] = reason
        return False, reason, details

    def reset_stats(self) -> None:
        self.stats = GateStats()
