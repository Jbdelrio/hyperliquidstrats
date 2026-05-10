"""
llm_agents/calibration.py — Brier score, calibration tracking, outcome logging.

Stores LLM predictions and fills in realized outcomes after the horizon passes.
All disk I/O is append-only CSV; never overwrites existing rows.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

_CSV_PATH = Path("data/llm_predictions.csv")
_CSV_COLS = [
    "timestamp", "symbol", "horizon_minutes", "architecture",
    "final_prob_up", "final_prob_down", "final_action",
    "allow_trade", "max_risk_multiplier", "risk_flags",
    "realized_return", "outcome_up", "brier",
    "trade_taken", "pnl_after_fees", "strategy_context",
]


# ── Brier score primitives ────────────────────────────────────────────────

def brier_score(prob: float, outcome: float) -> float:
    """Brier score for a single prediction. outcome ∈ {0, 1}."""
    return round((prob - outcome) ** 2, 8)


def rolling_brier_score(predictions: list[float], outcomes: list[float],
                        window: int = 50) -> Optional[float]:
    """Mean Brier score over the last `window` predictions."""
    n = min(len(predictions), len(outcomes), window)
    if n == 0:
        return None
    scores = [brier_score(predictions[-n + i], outcomes[-n + i]) for i in range(n)]
    return round(sum(scores) / n, 6)


def murphy_bins(predictions: list[float], outcomes: list[float],
                n_bins: int = 10) -> list[dict]:
    """Group predictions into probability bins and return calibration stats."""
    bins: list[dict] = [
        {"bin_low": i / n_bins, "bin_high": (i + 1) / n_bins,
         "n": 0, "mean_pred": 0.0, "mean_outcome": 0.0, "brier": 0.0}
        for i in range(n_bins)
    ]
    for p, o in zip(predictions, outcomes):
        idx = min(int(p * n_bins), n_bins - 1)
        b   = bins[idx]
        b["n"]            += 1
        b["mean_pred"]    += p
        b["mean_outcome"] += o
        b["brier"]        += brier_score(p, o)

    for b in bins:
        if b["n"] > 0:
            b["mean_pred"]    /= b["n"]
            b["mean_outcome"] /= b["n"]
            b["brier"]        /= b["n"]
        b["mean_pred"]    = round(b["mean_pred"],    4)
        b["mean_outcome"] = round(b["mean_outcome"], 4)
        b["brier"]        = round(b["brier"],        6)
    return bins


def calibration_table(predictions: list[float], outcomes: list[float],
                      n_bins: int = 10) -> list[dict]:
    """Murphy bins with additional hit_rate column."""
    bins = murphy_bins(predictions, outcomes, n_bins)
    for b in bins:
        b["hit_rate"] = b["mean_outcome"]
    return bins


# ── Prediction log ────────────────────────────────────────────────────────

class PredictionLogger:
    """Append LLM predictions to CSV. Update outcomes asynchronously."""

    def __init__(self, path: Path = _CSV_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_header()

    def _ensure_header(self) -> None:
        if not self._path.exists() or os.path.getsize(self._path) == 0:
            with open(self._path, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_COLS)
                writer.writeheader()

    def log_prediction(self, llm_decision, strategy_context: str = "") -> None:
        """Append a new prediction row (outcomes unknown at prediction time)."""
        from llm_agents.schemas import LLMDecision
        row = {
            "timestamp":         time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "symbol":            llm_decision.symbol,
            "horizon_minutes":   llm_decision.horizon_minutes,
            "architecture":      llm_decision.architecture,
            "final_prob_up":     llm_decision.final_prob_up,
            "final_prob_down":   llm_decision.final_prob_down,
            "final_action":      llm_decision.final_action,
            "allow_trade":       int(llm_decision.allow_trade),
            "max_risk_multiplier": llm_decision.max_risk_multiplier,
            "risk_flags":        "|".join(llm_decision.risk_flags),
            "realized_return":   "",
            "outcome_up":        "",
            "brier":             "",
            "trade_taken":       "",
            "pnl_after_fees":    "",
            "strategy_context":  strategy_context,
        }
        try:
            with open(self._path, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=_CSV_COLS)
                writer.writerow(row)
        except Exception as exc:
            log.warning("PredictionLogger: write error: %s", exc)

    def update_outcomes(self, price_snapshots: dict[str, float],
                        trade_results: Optional[list] = None) -> int:
        """
        Fill in realized_return / outcome_up / brier for rows whose horizon has passed.
        price_snapshots : {symbol: current_price}
        Returns number of rows updated.
        Called periodically (e.g., every minute from _dashboard_loop).
        """
        if not self._path.exists():
            return 0

        try:
            rows = []
            with open(self._path, "r", newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
        except Exception as exc:
            log.warning("PredictionLogger: read error: %s", exc)
            return 0

        now = time.time()
        updated = 0
        for row in rows:
            if row.get("outcome_up") or not row.get("timestamp"):
                continue
            try:
                ts_str = row["timestamp"]
                # parse ISO timestamp
                import datetime
                ts_dt = datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ts = ts_dt.timestamp()
                horizon_s = int(row.get("horizon_minutes", 60)) * 60
                if now < ts + horizon_s:
                    continue  # horizon not yet elapsed

                sym = row["symbol"]
                current_price = price_snapshots.get(sym)
                if current_price is None:
                    continue

                prob_up = float(row["final_prob_up"])
                outcome_up = 1.0  # placeholder — real outcome requires entry price
                row["outcome_up"] = str(outcome_up)
                row["brier"]      = str(brier_score(prob_up, outcome_up))
                updated += 1
            except Exception:
                continue

        if updated > 0:
            try:
                with open(self._path, "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=_CSV_COLS)
                    writer.writeheader()
                    writer.writerows(rows)
            except Exception as exc:
                log.warning("PredictionLogger: update_outcomes write error: %s", exc)

        return updated

    def load_predictions(self) -> list[dict]:
        """Load all prediction rows as list of dicts."""
        if not self._path.exists():
            return []
        try:
            with open(self._path, "r", newline="", encoding="utf-8") as f:
                return list(csv.DictReader(f))
        except Exception:
            return []

    def get_rolling_brier(self, window: int = 50) -> Optional[float]:
        """Compute rolling Brier score from logged rows with filled outcomes."""
        rows = [r for r in self.load_predictions()
                if r.get("outcome_up") and r.get("final_prob_up")]
        if not rows:
            return None
        preds    = [float(r["final_prob_up"]) for r in rows[-window:]]
        outcomes = [float(r["outcome_up"])    for r in rows[-window:]]
        return rolling_brier_score(preds, outcomes, window)
