"""
tests/test_diagnostic_and_llm_review.py — smoke + schema tests for the two
new analysis scripts:

  - scripts/diagnose_trading_frequency.py
  - scripts/run_llm_parameter_review.py

These tests build a tiny synthetic logs/runtime tree in a tmp_path, run the
scripts as subprocesses, and check the output files exist + have the
expected structure. They do NOT exercise the actual engine — that is what
the smoke tests in scripts/smoke_*.py do.
"""
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]


def _make_fake_run(root: Path, with_engine_config: bool = True) -> None:
    """Build a self-contained logs/runtime tree the scripts can read."""
    (root / "logs").mkdir(parents=True, exist_ok=True)
    (root / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "metrics_v9").mkdir(parents=True, exist_ok=True)

    # decisions_v9.csv — 60 SKIPs for OBImbalanceScalper with a cooldown rejection
    # plus 5 PLACE rows so the strategy looks alive.
    with open(root / "logs" / "decisions_v9.csv", "w", encoding="utf-8",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "symbol", "strategy", "decision", "reason",
                    "mid", "spread_bps", "hurst", "har_rv_forecast",
                    "kalman_fv", "obi", "buy_price", "sell_price", "size",
                    "notional_usd", "blocked_reason", "expected_net_profit_usd",
                    "expected_rr"])
        for i in range(60):
            w.writerow([str(1_700_000_000 + i), "BTC", "OBImbalanceScalper",
                        "FILTER_SKIP", "cooldown:120s_remaining",
                        "70000", "1.0", "", "", "", "0.1", "70000", "",
                        "", "50", "cooldown:120s_remaining", "1.0", "1.4"])
        for i in range(5):
            w.writerow([str(1_700_000_100 + i), "BTC", "OBImbalanceScalper",
                        "PLACE", "obimb_buy", "70000", "1.0", "", "", "",
                        "0.3", "70000", "", "", "50", "", "5.0", "2.0"])

    # fills_v9.csv — 3 fills, 2 wins / 1 loss for OBImbalanceScalper.
    with open(root / "logs" / "fills_v9.csv", "w", encoding="utf-8",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "symbol", "side", "notional", "entry", "exit",
                    "gross", "fee", "net", "hold_s", "reason", "strategy"])
        for i, net in enumerate([0.4, 0.2, -0.3]):
            w.writerow([f"{1_700_000_200 + i}", "BTC", "BUY", "50",
                        "70000", "70010", f"{net+0.03}", "0.03", f"{net}",
                        "30", "take_profit" if net > 0 else "stop_loss",
                        "OBImbalanceScalper"])

    # risk_events.csv — empty header.
    with open(root / "logs" / "risk_events.csv", "w", encoding="utf-8",
              newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ts", "strategy", "symbol", "action", "requested_notional",
                    "allowed", "block_reason"])

    # engine log skeleton.
    (root / "logs" / "engine_v9.log").write_text(
        "2026-05-23 10:00:00 INFO __main__ Engine V9 running.\n",
        encoding="utf-8")

    # strategy_status.json
    with open(root / "runtime" / "strategy_status.json", "w",
              encoding="utf-8") as fh:
        json.dump([
            {"name": "OBImbalanceScalper", "state": "ACTIVE",
             "enabled": True, "capital_allocated_usd": 500,
             "warmup_status": {}},
        ], fh)

    # data_feed_status.json
    with open(root / "runtime" / "data_feed_status.json", "w",
              encoding="utf-8") as fh:
        json.dump({"ts": 1_700_000_300, "data_feed_health": {
            "per_symbol": {
                "BTC": {"book_updates_per_sec": 2.5, "trades_per_sec": 1.2,
                        "last_book_age_s": 0.1, "last_trade_age_s": 0.5,
                        "is_book_stale": False, "spread_bps_mean": 1.0},
            },
            "reconnections": 0, "queue_drops": 0,
            "book_updates_count": 12000, "trade_events_count": 6000,
        }, "market_quality_gate_stats": {"total_evaluated": 100,
                                          "total_blocked": 20,
                                          "blocks_by_reason": {"warmup": 12}}},
                  fh)

    if with_engine_config:
        with open(root / "runtime" / "engine_config.json", "w",
                  encoding="utf-8") as fh:
            json.dump({"exchange": "hyperliquid", "paper": True,
                       "started_at": 1_700_000_000,
                       "session_id": "abcdef123456",
                       "config_path": str(
                           _REPO / "config" / "presets" /
                           "paper_500_all_active.json"),
                       "pid": 12345}, fh)


# ---------------------------------------------------------------------------
# diagnose_trading_frequency
# ---------------------------------------------------------------------------

def test_diagnostic_script_runs_and_writes_report(tmp_path):
    _make_fake_run(tmp_path)
    out = tmp_path / "reports" / "diag.md"
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "diagnose_trading_frequency.py"),
         "--since", "1700000000", "--out", str(out)],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "Trading Frequency Diagnostic" in body
    assert "OBImbalanceScalper" in body
    assert "Headlines" in body


def test_diagnostic_handles_missing_engine_config(tmp_path):
    _make_fake_run(tmp_path, with_engine_config=False)
    out = tmp_path / "reports" / "diag.md"
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "diagnose_trading_frequency.py"),
         "--since", "1700000000", "--out", str(out)],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists()


# ---------------------------------------------------------------------------
# run_llm_parameter_review
# ---------------------------------------------------------------------------

def test_llm_review_proposes_cooldown_patch(tmp_path):
    _make_fake_run(tmp_path)
    out = tmp_path / "reports" / "review.md"
    patch = tmp_path / "runtime" / "proposed_config_patch.json"
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "run_llm_parameter_review.py"),
         "--logs", "logs", "--runtime", "runtime",
         "--out", str(out), "--patch", str(patch),
         "--min-rejects", "10", "--since", "1700000000"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    assert out.exists() and patch.exists()
    body = out.read_text(encoding="utf-8")
    assert "LLM Parameter Review" in body
    data = json.load(open(patch, encoding="utf-8"))
    assert isinstance(data["patches"], list)
    # We injected 60 cooldown rejections → at least one cooldown-related patch.
    cool = [p for p in data["patches"] if "cooldown" in p["parameter"].lower()]
    assert cool, f"expected a cooldown patch, got {data['patches']}"


def test_llm_review_patch_schema_is_complete(tmp_path):
    _make_fake_run(tmp_path)
    patch = tmp_path / "runtime" / "proposed_config_patch.json"
    out = tmp_path / "reports" / "review.md"
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "run_llm_parameter_review.py"),
         "--logs", "logs", "--runtime", "runtime",
         "--out", str(out), "--patch", str(patch),
         "--min-rejects", "10", "--since", "1700000000"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.load(open(patch, encoding="utf-8"))
    required = {"strategy", "parameter", "current_value", "proposed_value",
                "reason", "expected_effect", "confidence",
                "apply_automatically"}
    eff_required = {"trade_frequency", "risk", "net_pnl"}
    for p in data["patches"]:
        missing = required - set(p.keys())
        assert not missing, f"missing keys in patch: {missing}"
        assert isinstance(p["confidence"], (int, float))
        assert 0.0 <= p["confidence"] <= 1.0
        # SAFETY: must NEVER be applied automatically.
        assert p["apply_automatically"] is False
        assert eff_required <= set(p["expected_effect"].keys())


def test_llm_review_skips_low_count_strategies(tmp_path):
    """Strategies with very few rejections must not produce noise."""
    _make_fake_run(tmp_path)
    out = tmp_path / "reports" / "review.md"
    patch = tmp_path / "runtime" / "proposed_config_patch.json"
    proc = subprocess.run(
        [sys.executable, str(_REPO / "scripts" / "run_llm_parameter_review.py"),
         "--logs", "logs", "--runtime", "runtime",
         "--out", str(out), "--patch", str(patch),
         "--min-rejects", "10000", "--since", "1700000000"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.load(open(patch, encoding="utf-8"))
    # min-rejects 10000 → no per-strategy patches; globals can still appear.
    per_strat = [p for p in data["patches"] if p["strategy"] != "(global)"]
    assert per_strat == []
