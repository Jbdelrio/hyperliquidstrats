"""
smoke_new_strategies.py — Quick sanity check for the 5 new Phase-2 strategies.

Usage:
    python scripts/smoke_new_strategies.py

Exits 0 on success, 1 on any failure.
"""
import sys
import time
import types

# ---------------------------------------------------------------------------
# Minimal stubs so strategies don't need a full engine
# ---------------------------------------------------------------------------

def _make_config(name, cls_name, coins=None):
    from strategies.base_strategy import StrategyConfig
    return StrategyConfig(
        name=name,
        enabled=True,
        capital_allocated_usd=500,
        max_positions=2,
        max_position_size_usd=200,
        coins=coins or ["BTC", "ETH"],
        params={},
    )


class FakeBook:
    best_bid = 60_000.0
    best_ask = 60_010.0
    mid = 60_005.0

    def imbalance(self, n_levels: int = 5) -> float:
        return 0.45  # strong buy pressure


class FakeBar:
    symbol = "BTC"
    ts = time.time()
    open = 59_900.0
    high = 60_100.0
    low  = 59_800.0
    close = 60_050.0
    volume_usd = 1_000_000.0
    return_1m  = 0.0008


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

PASS = []
FAIL = []


def check(name, fn):
    try:
        fn()
        PASS.append(name)
        print(f"  [OK]  {name}")
    except Exception as exc:
        FAIL.append(name)
        print(f"  [FAIL] {name}: {exc}")


def test_spot_perp_basis():
    from strategies.spot_perp_basis import SpotPerpBasisStrategy
    cfg = _make_config("SpotPerpBasis", "SpotPerpBasisStrategy")
    s = SpotPerpBasisStrategy(cfg)

    book = FakeBook()
    ts = time.time()

    # Scanner mode (no spot → no trade)
    dec = s.on_orderbook_update("BTC", book, ts)
    assert dec is None, "Expected None in scanner mode"

    # Calibration data
    cal = s.get_calibration_data("BTC")
    assert "basis_bps" in cal
    assert cal["mode"] == "perp_only_basis_proxy"

    # Stats
    stats = s.get_stats()
    assert "open_positions_count" in stats


def test_funding_carry_hedged():
    from strategies.funding_carry_hedged import FundingCarryHedgedStrategy
    from strategies.base_strategy import BarData
    cfg = _make_config("FundingCarryHedged", "FundingCarryHedgedStrategy")
    s = FundingCarryHedgedStrategy(cfg)
    ts = time.time()

    bar = BarData("BTC", ts, 60000, 60100, 59900, 60050, 1e6, 0.001)

    # No funding data yet → None
    dec = s.on_bar_minute("BTC", bar, ts)
    assert dec is None, "Expected None without funding data"

    # Inject funding and history
    s._funding_raw["BTC"] = 0.0002   # 2bps/h (positive → short if allowed)
    s._funding_hist["BTC"].extend([0.0002, 0.0002, 0.0003])

    dec = s.on_bar_minute("BTC", bar, ts + 5)
    # allow_unhedged_perp=False by default → scanner returns None
    assert dec is None, "Expected None (scanner mode)"

    cal = s.get_calibration_data("BTC")
    assert cal["funding_bps_per_hour"] is not None
    assert cal["allow_unhedged_perp"] is False

    stats = s.get_stats()
    assert "open_positions_count" in stats


def test_orderbook_imbalance_scalper():
    from strategies.orderbook_imbalance_scalper import OrderBookImbalanceScalper
    cfg = _make_config("OBImbalance", "OrderBookImbalanceScalper")
    cfg.params["imbalance_entry_threshold"] = 0.30
    cfg.params["min_persistence_updates"] = 3
    cfg.params["max_hold_seconds"] = 120
    s = OrderBookImbalanceScalper(cfg)

    book = FakeBook()  # imbalance() returns 0.45 > 0.30
    ts = time.time()

    # Feed persistence updates
    for _ in range(3):
        dec = s.on_orderbook_update("BTC", book, ts)
        ts += 0.1
    # After 3 consecutive strong-imbalance updates, should fire BUY
    assert dec is not None and dec.action == "PLACE_BUY", f"Expected PLACE_BUY, got {dec}"

    cal = s.get_calibration_data("BTC")
    assert cal["signal"] == "buy_pressure"

    stats = s.get_stats()
    assert "open_positions_count" in stats


def test_volatility_regime_breakout():
    from strategies.volatility_regime_breakout import VolatilityRegimeBreakoutStrategy
    from strategies.base_strategy import BarData
    cfg = _make_config("VolBreakout", "VolatilityRegimeBreakoutStrategy")
    cfg.params["donchian_period"] = 5
    cfg.params["atr_period"] = 5
    cfg.params["high_vol_threshold_bps"] = 0.1  # very low → always high regime in test
    s = VolatilityRegimeBreakoutStrategy(cfg)

    ts = time.time()
    base = 60_000.0

    # Feed 10 bars with growing closes (to build channel)
    for i in range(10):
        bar = BarData("BTC", ts + i * 60,
                      base, base + 50 * (i + 1), base - 20, base + 40 * i, 1e6, 0.001)
        dec = s.on_bar_minute("BTC", bar, ts + i * 60)

    cal = s.get_calibration_data("BTC")
    assert "regime" in cal
    assert "donchian_high" in cal

    stats = s.get_stats()
    assert "open_positions_count" in stats


def test_meta_alpha_strategy():
    from strategies.meta_alpha_strategy import MetaAlphaStrategy
    from strategies.funding_carry_hedged import FundingCarryHedgedStrategy
    from strategies.base_strategy import BarData

    cfg_meta = _make_config("MetaAlpha", "MetaAlphaStrategy")
    cfg_meta.params["min_agreement_score"] = 1  # quorum of 1 for test
    meta = MetaAlphaStrategy(cfg_meta)

    # Register a peer that has a directional bias
    cfg_peer = _make_config("FundingCarryHedged", "FundingCarryHedgedStrategy")
    peer = FundingCarryHedgedStrategy(cfg_peer)
    peer._funding_raw["BTC"] = 0.0005  # positive → short_perp_collect bias
    peer._funding_hist["BTC"].extend([0.0005, 0.0005, 0.0006])

    meta.register_peer("funding_carry", peer)

    ts = time.time()
    bar = BarData("BTC", ts, 60000, 60100, 59900, 60050, 1e6, 0.001)
    dec = meta.on_bar_minute("BTC", bar, ts)

    # peer votes "short_perp_collect" → SELL
    assert dec is not None and dec.action == "PLACE_SELL", f"Expected PLACE_SELL, got {dec}"

    cal = meta.get_calibration_data("BTC")
    assert cal["peers_registered"] == ["funding_carry"]
    assert cal["net_score"] == -1

    stats = meta.get_stats()
    assert stats["peers_registered"] == 1


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("  Smoke test — 5 new Phase-2 strategies")
    print("=" * 60)

    check("SpotPerpBasisStrategy",             test_spot_perp_basis)
    check("FundingCarryHedgedStrategy",        test_funding_carry_hedged)
    check("OrderBookImbalanceScalper",         test_orderbook_imbalance_scalper)
    check("VolatilityRegimeBreakoutStrategy",  test_volatility_regime_breakout)
    check("MetaAlphaStrategy",                 test_meta_alpha_strategy)

    print()
    print(f"Results: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        sys.exit(1)
    else:
        print("All smoke tests passed.")
        sys.exit(0)
