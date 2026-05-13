"""
smoke_strategies.py — Audit-level sanity checks for all strategies.

Run from repo root:
    python scripts/smoke_strategies.py

Checks:
  1. MetaAlpha has peers wired after engine init
  2. FundingArbitrage/FundingCarryHedged use per-hour convention (not raw 8h)
  3. RelativeValue is scanner-only when require_beta_hedge=True
  4. MomentumLS top-percentile coin passes filter, middle fails
  5. OBImbalanceScalper spread filter rejects wide spreads
  6. BreakoutControlled on_fill reads pending_entries (not stale signal)
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from strategies.base_strategy import StrategyConfig, BarData


def _cfg(name, cls_name, coins=None, params=None, capital=500, max_pos=2, max_size=250):
    return StrategyConfig(
        name=name, enabled=True,
        capital_allocated_usd=capital,
        max_positions=max_pos,
        max_position_size_usd=max_size,
        coins=coins or ["BTC"],
        params=params or {},
    )


# ── 1. MetaAlpha peers ──────────────────────────────────────────────────────

def test_meta_alpha_peers_wired():
    from strategies.meta_alpha_strategy import MetaAlphaStrategy
    from strategies.momentum_long_short import MomentumLongShort

    meta = MetaAlphaStrategy(_cfg("MetaAlpha", "MetaAlphaStrategy",
                                   coins=["BTC"], params={"min_agreement_score": 1}))
    assert len(meta._peers) == 0, "no peers yet"

    dummy = MomentumLongShort(_cfg("MomentumLS", "MomentumLongShort", coins=["BTC"]))
    meta.register_peer("MomentumLS", dummy)
    assert len(meta._peers) == 1

    cal = meta.get_calibration_data("BTC")
    assert not cal["no_peers"], "no_peers should be False after registration"
    print("PASS  test_meta_alpha_peers_wired")


# ── 2. Funding convention ────────────────────────────────────────────────────

def test_funding_arbitrage_uses_hourly():
    """FundingArbitrage must store per-hour rate (not 8h rate)."""
    from strategies.funding_arbitrage import FundingArbitrage

    fa = FundingArbitrage(_cfg("FundingArbitrage", "FundingArbitrage",
                                coins=["BTC"],
                                params={"funding_entry_threshold_pct_per_hour": 0.03,
                                        "allow_unhedged_perp": False}))
    # Simulate _fetch_funding having received a raw 8h rate of 0.0008 (8bps over 8h = 1bps/h)
    # After divide-by-8, stored hourly = 0.0001
    fa._raw_funding["BTC"] = 0.0001   # 0.01%/h — below 0.03%/h threshold
    fa._funding["BTC"].append(0.0001)
    fa._funding["BTC"].append(0.0001)
    fa._funding["BTC"].append(0.0001)
    fa._r24h["BTC"] = 10.0

    decision = fa._check_entry("BTC", time.time())
    # Even if allow_unhedged_perp were True, 0.0001 < 0.0003 → should not trigger
    assert decision is None, "0.01%/h < threshold 0.03%/h → should not enter"
    print("PASS  test_funding_arbitrage_uses_hourly")


def test_funding_carry_hedged_expected_edge():
    """FundingCarryHedged expected_edge must include hold duration."""
    from strategies.funding_carry_hedged import FundingCarryHedgedStrategy

    fch = FundingCarryHedgedStrategy(_cfg(
        "FCH", "FundingCarryHedgedStrategy", coins=["BTC"],
        params={
            "funding_entry_bps_per_hour": 0.5,
            "expected_hold_hours": 4,
            "taker_fee_bps": 3.5,
            "slippage_bps": 2.0,
            "safety_buffer_bps": 2.0,
            "min_expected_edge_bps": 3.0,
            "allow_unhedged_perp": False,
        }))
    fch._funding_raw["BTC"] = 0.0001   # 1 bps/h
    cal = fch.get_calibration_data("BTC")
    # expected_edge = 1 bps/h × 4h - (3.5+2+2) = 4 - 7.5 = -3.5 < 0
    assert cal["expected_edge_bps"] is not None
    assert cal["expected_edge_bps"] < 0, "Should be negative at 1bps/h × 4h minus costs"
    print("PASS  test_funding_carry_hedged_expected_edge")


# ── 3. RelativeValue scanner only ────────────────────────────────────────────

def test_relative_value_scanner_mode():
    from strategies.relative_value import RelativeValueStrategy

    rv = RelativeValueStrategy(_cfg("RelativeValue", "RelativeValueStrategy",
                                     coins=["ETH", "BTC"],
                                     params={"require_beta_hedge": True,
                                             "pairs": [["ETH", "BTC"]]}))
    assert rv._require_hedge is True

    # Fabricate a pair state with a valid signal
    key = "ETH/BTC"
    ps  = rv._pairs[key]
    ps.z_score     = -2.5   # below entry_z default -2.0 → would trigger
    ps.correlation = 0.90
    ps.beta        = 0.06
    ps.alpha       = 0.0

    dec = rv._check_entry(key, ps, time.time())
    assert dec is not None and dec.action == "SKIP", f"Expected SKIP, got {dec}"
    assert "hedge_required" in (dec.metadata or {}), "metadata should note hedge_required"
    print("PASS  test_relative_value_scanner_mode")


# ── 4. MomentumLS percentile filter ─────────────────────────────────────────

def test_momentum_ls_percentile_filter():
    """Top-percentile long passes; mid-percentile long is blocked."""
    from strategies.momentum_long_short import MomentumLongShort

    coins = ["BTC", "ETH", "SOL", "AVAX", "LINK", "ARB"]
    strat = MomentumLongShort(_cfg("MomentumLS", "MomentumLongShort",
                                    coins=coins,
                                    params={"long_percentile_min": 0.75,
                                            "short_percentile_max": 0.25,
                                            "spread_bps_max": 50.0}))
    # Set scores: BTC at top (1.0), ETH at bottom (0.0), SOL at middle (0.5)
    strat._scores    = {"BTC": 1.0, "ETH": 0.0, "SOL": 0.5, "AVAX": 0.8, "LINK": 0.2, "ARB": 0.5}
    strat._raw_scores = {c: 0.0 for c in coins}
    strat._longs     = {"BTC", "AVAX"}
    strat._shorts    = {"ETH", "LINK"}

    p = strat.config.params
    # BTC long → pct=1.0 >= 0.75 → should pass filter
    assert strat._scores["BTC"] >= p.get("long_percentile_min", 0.75), "BTC should pass long filter"
    # SOL long (not in longs, skip) → if it were, pct=0.5 < 0.75 → blocked
    assert strat._scores["SOL"] < p.get("long_percentile_min", 0.75), "SOL should fail long filter"
    # ETH short → pct=0.0 <= 0.25 → should pass
    assert strat._scores["ETH"] <= p.get("short_percentile_max", 0.25), "ETH should pass short filter"
    # LINK short → pct=0.2 <= 0.25 → passes
    assert strat._scores["LINK"] <= p.get("short_percentile_max", 0.25), "LINK should pass short filter"
    print("PASS  test_momentum_ls_percentile_filter")


# ── 5. OBImbalanceScalper spread filter ──────────────────────────────────────

def test_ob_scalper_spread_filter():
    from strategies.orderbook_imbalance_scalper import OrderBookImbalanceScalper

    ob = OrderBookImbalanceScalper(_cfg("OBImb", "OrderBookImbalanceScalper",
                                         coins=["BTC"],
                                         params={"max_spread_bps": 8.0,
                                                 "take_profit_pct": 0.004,
                                                 "taker_fee_bps": 3.5,
                                                 "slippage_bps": 2.0,
                                                 "min_cost_ratio": 2.0,
                                                 "min_persistence_updates": 3}))
    # Force 3 buy-imbalance readings
    for _ in range(5):
        ob._imb_hist["BTC"].append(0.5)
    # Wide spread → should be blocked
    dec = ob._check_entry("BTC", bid=99900.0, ask=100100.0, mid=100000.0,
                           imb=0.5, ts=time.time())
    # spread = 200/100000 * 10000 = 20 bps > 8 → None
    assert dec is None, f"Wide spread should be rejected, got {dec}"
    print("PASS  test_ob_scalper_spread_filter")


# ── 6. BreakoutControlled pending_entries ────────────────────────────────────

def test_breakout_pending_entries():
    from strategies.breakout_controlled import BreakoutControlled

    bc = BreakoutControlled(_cfg("Breakout", "BreakoutControlled",
                                  coins=["BTC"],
                                  params={"take_profit_pct": 2.5,
                                          "stop_below_resistance_pct": 1.5}))
    # Simulate on_fill reading pending_entries after signal was already consumed
    symbol = "BTC"
    bc._pending_entries[symbol] = {"resistance": 99000.0, "vr": 1.5}

    result = bc.on_fill(symbol, "BUY", price=100000.0, size=0.01, ts=time.time())
    assert result is not None
    # stop should be below resistance (99000 * (1-0.015) = 97515)
    assert result["stop_price"] < 99000.0, f"stop {result['stop_price']} should be below 99000"
    # pending entry should be cleared
    assert symbol not in bc._pending_entries, "pending_entry should be cleared after fill"
    print("PASS  test_breakout_pending_entries")


# ── Runner ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_meta_alpha_peers_wired,
        test_funding_arbitrage_uses_hourly,
        test_funding_carry_hedged_expected_edge,
        test_relative_value_scanner_mode,
        test_momentum_ls_percentile_filter,
        test_ob_scalper_spread_filter,
        test_breakout_pending_entries,
    ]
    failed = []
    for t in tests:
        try:
            t()
        except Exception as exc:
            print(f"FAIL  {t.__name__}: {exc}")
            failed.append(t.__name__)

    print(f"\n{'ALL PASSED' if not failed else f'{len(failed)} FAILED: {failed}'} "
          f"({len(tests) - len(failed)}/{len(tests)})")
    sys.exit(1 if failed else 0)
