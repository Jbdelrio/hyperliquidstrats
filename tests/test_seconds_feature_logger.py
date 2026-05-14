"""Tests for `monitoring/seconds_feature_logger.py`."""
import csv
import time
from pathlib import Path

from monitoring.seconds_feature_logger import (
    FIELDNAMES,
    SecondsFeatureLogger,
)


def test_header_written(tmp_path: Path):
    p = tmp_path / "feat.csv"
    SecondsFeatureLogger(str(p))
    with open(p) as fh:
        header = next(csv.reader(fh))
    assert header == FIELDNAMES


def test_log_accepts_first_row_rejects_within_interval(tmp_path: Path):
    p = tmp_path / "feat.csv"
    lg = SecondsFeatureLogger(str(p), min_interval_s=1.0, flush_rows=1)
    ts = time.time()
    feats = {"symbol": "BTC", "ts": ts, "mid": 100.0, "spread_bps": 5.0}
    assert lg.log(feats) is True
    # Same ts → rate-limited
    assert lg.log(feats) is False
    # +0.9 s — still inside interval
    feats2 = dict(feats); feats2["ts"] = ts + 0.9
    assert lg.log(feats2) is False
    # +1.5 s → accepted
    feats3 = dict(feats); feats3["ts"] = ts + 1.5
    assert lg.log(feats3) is True


def test_rows_written_have_expected_columns(tmp_path: Path):
    p = tmp_path / "feat.csv"
    lg = SecondsFeatureLogger(str(p), min_interval_s=0, flush_rows=1)
    feats = {f: 0.0 for f in FIELDNAMES if f not in ("symbol", "datetime")}
    feats["symbol"] = "ETH"
    feats["ts"] = time.time()
    feats["enough_data"] = True
    feats["book_stale"] = False
    assert lg.log(feats) is True
    lg.flush()
    with open(p) as fh:
        rows = list(csv.DictReader(fh))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH"
    # bool serialization
    assert rows[0]["enough_data"] == "1"
    assert rows[0]["book_stale"] == "0"
