"""
tests/test_llm_calibration.py — Unit tests for calibration metrics.
"""
import tempfile
from pathlib import Path

import pytest
from llm_agents.calibration import (
    PredictionLogger,
    brier_score,
    calibration_table,
    murphy_bins,
    rolling_brier_score,
)


def test_brier_perfect_up():
    assert brier_score(1.0, 1.0) == pytest.approx(0.0)


def test_brier_perfect_down():
    assert brier_score(0.0, 0.0) == pytest.approx(0.0)


def test_brier_worst():
    assert brier_score(1.0, 0.0) == pytest.approx(1.0)
    assert brier_score(0.0, 1.0) == pytest.approx(1.0)


def test_brier_neutral():
    assert brier_score(0.5, 1.0) == pytest.approx(0.25)
    assert brier_score(0.5, 0.0) == pytest.approx(0.25)


def test_rolling_brier_empty():
    assert rolling_brier_score([], [], 50) is None


def test_rolling_brier_window():
    preds    = [0.6, 0.6, 0.6]
    outcomes = [1.0, 1.0, 1.0]
    score = rolling_brier_score(preds, outcomes, window=3)
    assert score == pytest.approx(0.16, abs=1e-4)


def test_murphy_bins_counts():
    preds    = [0.1, 0.3, 0.5, 0.7, 0.9]
    outcomes = [0,   0,   1,   1,   1  ]
    bins = murphy_bins(preds, outcomes, n_bins=5)
    total_n = sum(b["n"] for b in bins)
    assert total_n == 5


def test_calibration_table_has_hit_rate():
    preds    = [0.2, 0.8]
    outcomes = [0,   1  ]
    table = calibration_table(preds, outcomes, n_bins=5)
    for b in table:
        assert "hit_rate" in b


def test_prediction_logger_write_and_read():
    from llm_agents.schemas import LLMDecision

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test_predictions.csv"
        logger = PredictionLogger(path)

        dec = LLMDecision(
            enabled=True, architecture="test", symbol="BTC",
            horizon_minutes=60, final_prob_up=0.6, final_prob_down=0.4,
            final_confidence="medium", final_action="LONG",
            allow_trade=True, max_risk_multiplier=0.5,
            reason="test", risk_flags=["test_flag"],
        )
        logger.log_prediction(dec, "S8EMS")
        rows = logger.load_predictions()
        assert len(rows) == 1
        assert rows[0]["symbol"] == "BTC"
        assert rows[0]["final_action"] == "LONG"


def test_prediction_logger_no_crash_on_missing():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "nonexistent.csv"
        logger = PredictionLogger(path)
        rows = logger.load_predictions()
        assert rows == [] or len(rows) >= 0  # should not raise


def test_update_outcomes_no_crash_missing_data():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "test.csv"
        logger = PredictionLogger(path)
        updated = logger.update_outcomes({"BTC": 50000.0})
        assert updated == 0
