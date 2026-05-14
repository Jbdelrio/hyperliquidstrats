"""
funding_logger.py — CSV loggers for funding snapshots, opportunities,
and paper positions.

Three independent CSVs, all under `logs/` :
    logs/funding_snapshots.csv      — one row per snapshot fetch
    logs/funding_opportunities.csv  — one row per scanner output
    logs/funding_positions.csv      — one row per paper position state update

Each logger writes its header automatically on first write.
"""
from __future__ import annotations

import csv
import math
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


_SNAPSHOT_FIELDS = [
    "ts", "datetime",
    "exchange", "symbol",
    "funding_rate", "funding_rate_bps",
    "next_funding_time",
    "mark_price", "oracle_price", "index_price",
    "open_interest",
]

_OPPORTUNITY_FIELDS = [
    "ts", "datetime", "symbol",
    "long_exchange", "short_exchange", "direction", "mode",
    "funding_hl", "funding_aster", "spread_bps",
    "notional_usd",
    "expected_funding_bps", "expected_net_bps", "expected_net_usd",
    "estimated_cost_bps",
    "basis_bps", "liquidity_score", "stability_score", "risk_score",
    "horizon_hours",
    "decision", "reason",
]

_POSITION_FIELDS = [
    "ts", "datetime",
    "position_id", "symbol", "mode",
    "long_exchange", "short_exchange",
    "long_notional", "short_notional",
    "entry_basis_bps", "current_basis_bps",
    "funding_collected_usd", "unrealized_pnl_usd", "net_pnl_usd",
    "hedge_error_usd", "liquidation_buffer",
    "status",
]


def _fmt(v):
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        return f"{v:.10g}"
    if isinstance(v, bool):
        return "1" if v else "0"
    return v


def _ensure_header(path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as fh:
            csv.writer(fh).writerow(fields)


def _now_dt(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


class _BaseCsvLogger:

    def __init__(self, path: str, fields: list[str]):
        self.path = Path(path)
        self.fields = fields
        self._lock = threading.Lock()
        _ensure_header(self.path, self.fields)

    def _write_rows(self, rows: Iterable[dict]) -> None:
        with self._lock:
            with open(self.path, "a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=self.fields, extrasaction="ignore")
                for r in rows:
                    out = {f: _fmt(r.get(f)) for f in self.fields}
                    w.writerow(out)


class FundingSnapshotLogger(_BaseCsvLogger):

    def __init__(self, path: str = "logs/funding_snapshots.csv"):
        super().__init__(path, _SNAPSHOT_FIELDS)

    def log_snapshots(self, snaps) -> None:
        ts = time.time()
        rows = []
        for s in snaps:
            rows.append({
                "ts": ts, "datetime": _now_dt(ts),
                "exchange": s.exchange, "symbol": s.symbol,
                "funding_rate": s.funding_rate,
                "funding_rate_bps": s.funding_rate_bps,
                "next_funding_time": s.next_funding_time,
                "mark_price": s.mark_price,
                "oracle_price": s.oracle_price,
                "index_price": s.index_price,
                "open_interest": s.open_interest,
            })
        self._write_rows(rows)


class FundingOpportunityLogger(_BaseCsvLogger):

    def __init__(self, path: str = "logs/funding_opportunities.csv"):
        super().__init__(path, _OPPORTUNITY_FIELDS)

    def log(self, opp_rows: Iterable[dict]) -> None:
        ts = time.time()
        rows = []
        for r in opp_rows:
            row = dict(r)
            row.setdefault("ts", ts)
            row.setdefault("datetime", _now_dt(row["ts"]))
            rows.append(row)
        self._write_rows(rows)


class FundingPositionLogger(_BaseCsvLogger):

    def __init__(self, path: str = "logs/funding_positions.csv"):
        super().__init__(path, _POSITION_FIELDS)

    def log(self, position_rows: Iterable[dict]) -> None:
        ts = time.time()
        rows = []
        for r in position_rows:
            row = dict(r)
            row.setdefault("ts", ts)
            row.setdefault("datetime", _now_dt(row["ts"]))
            rows.append(row)
        self._write_rows(rows)
