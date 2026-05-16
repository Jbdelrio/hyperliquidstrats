#!/usr/bin/env python
"""
smoke_all_strategies_adaptive.py — Load the adaptive preset, instantiate
every strategy, exercise their hooks with synthetic data, and verify
no strategy crashes.

Usage :
    python scripts/smoke_all_strategies_adaptive.py
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from engine_v9 import EngineV9
from strategies.base_strategy import BarData
from data.orderbook_manager import TradeEvent


@dataclass
class _Book:
    bids: list
    asks: list

    @property
    def best_bid(self) -> Optional[float]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[float]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid(self) -> Optional[float]:
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2

    @property
    def spread_bps(self):
        if self.best_bid and self.best_ask:
            m = (self.best_bid + self.best_ask) / 2
            return (self.best_ask - self.best_bid) / m * 10_000

    def imbalance(self, n_levels=5):
        b = sum(sz for _, sz in self.bids[:n_levels])
        a = sum(sz for _, sz in self.asks[:n_levels])
        return (b - a) / (b + a) if (b + a) > 0 else 0.0


def _book(mid=100.0, spread_bps=4.0):
    half = mid * spread_bps / 20_000
    bid = mid - half
    ask = mid + half
    bids = [(bid - i * 0.01, 2.0) for i in range(10)]
    asks = [(ask + i * 0.01, 2.0) for i in range(10)]
    return _Book(bids, asks)


def _bar(symbol, close=100.0):
    return BarData(symbol=symbol, ts=time.time(), open=close * 0.999,
                   high=close * 1.001, low=close * 0.998, close=close,
                   volume_usd=1000.0, return_1m=0.0)


def main() -> int:
    print("Loading adaptive preset...")
    e = EngineV9(
        config_path="config/presets/paper_500_all_strategies_adaptive.json",
        paper=True,
    )
    enabled = [n for n, s in e.manager.strategies.items() if s.enabled]
    print(f"Engine initialised. {len(enabled)} enabled strategies.")
    print(f"  Trading : {sum(1 for n in enabled if e.manager.strategies[n].config.max_positions > 0)}")
    print(f"  Research: {sum(1 for n in enabled if e.manager.strategies[n].config.max_positions == 0)}")

    failures = []

    # 1. Each strategy responds to a fake orderbook_update + trade_update + bar
    for name, strat in e.manager.strategies.items():
        if not strat.enabled:
            continue
        coin = strat.config.coins[0] if strat.config.coins else "BTC"
        b = _book()
        try:
            strat.on_orderbook_update(coin, b, time.time())
        except Exception as exc:
            failures.append((name, "on_orderbook_update", str(exc)))
        fake_trade = TradeEvent(symbol=coin, price=100.0, size=0.5,
                                volume_usd=50.0, side="B",
                                best_bid=99.99, best_ask=100.01,
                                timestamp=time.time(), recv_ts=time.time(),
                                latency_ms=50.0)
        try:
            strat.on_trade_update(coin, fake_trade, time.time())
        except Exception as exc:
            failures.append((name, "on_trade_update", str(exc)))
        try:
            strat.on_bar_minute(coin, _bar(coin), time.time())
        except Exception as exc:
            failures.append((name, "on_bar_minute", str(exc)))
        # 2. Optional hooks
        try:
            strat.on_second_features(coin, {"symbol": coin, "mid": 100.0,
                                            "enough_data": False,
                                            "book_stale": False}, time.time())
        except Exception as exc:
            failures.append((name, "on_second_features", str(exc)))
        # 3. data_requirements + warmup_status survive a call
        try:
            req = strat.data_requirements()
            assert isinstance(req, dict)
            ws = strat.warmup_status()
            assert isinstance(ws, dict)
        except Exception as exc:
            failures.append((name, "data_requirements", str(exc)))

    # 4. Manual command smoke
    print("Smoke-testing manual commands...")
    r = e._process_control_command({
        "command": "manual_set_param",
        "args": {"strategy": "OBImbalanceScalper",
                 "param_name": "imbalance_entry_threshold",
                 "value": 0.42, "ttl_seconds": 60, "reason": "smoke"},
    }, time.time())
    assert r["ok"], r

    if failures:
        print(f"\n[FAIL] {len(failures)} strategy hook failures:")
        for n, hook, err in failures:
            print(f"  {n}.{hook}: {err}")
        return 1
    print("\nOK — all strategies exercised without crashing.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
