"""backtesting — offline metrics & a minimal bar-replay backtest engine."""
from .metrics import compute_metrics
from .data_loader import load_fills_as_trades

__all__ = ["compute_metrics", "load_fills_as_trades"]
