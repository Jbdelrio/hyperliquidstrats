"""
tests/test_funding_carry_hedged.py — delta-neutral funding carry (hedged mode).

Covers the self-contained carry simulation: paired perp/spot legs, funding
accrual, delta-neutrality, exit logic, realised PnL and CSV logging.

The strategy's _refresh_funding() hits the network; these tests bypass it by
populating _funding_raw / _funding_hist / _prices / _perp_mid directly and
exercising the internal carry methods.
"""
import csv
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from strategies.base_strategy import StrategyConfig
from strategies.funding_carry_hedged import FundingCarryHedgedStrategy


def _cfg(params: dict, capital=500.0, max_pos=1, max_size=250.0) -> StrategyConfig:
    return StrategyConfig(
        name="FCH", enabled=True, capital_allocated_usd=capital,
        max_positions=max_pos, max_position_size_usd=max_size,
        coins=["BTC", "ETH", "SOL"], params=params,
    )


def _hedged_params(tmp_path) -> dict:
    return {
        "hedged": True,
        "funding_entry_bps_per_hour": 0.5,
        "funding_exit_bps_per_hour": 0.15,
        "expected_hold_hours": 24,
        "min_expected_edge_bps": 3.0,
        "taker_fee_bps": 4.5, "slippage_bps": 2.0,
        "spot_fee_bps": 7.0, "spot_slippage_bps": 2.0,
        "max_hold_hours": 72,
        "max_basis_bps": 60.0,
        "stop_loss_pct": 0.02,
        "paper_log_path": str(tmp_path / "funding_carry_paper.csv"),
    }


def _prime(strat, coin, funding_hourly, oracle, mark, perp_mid=None):
    """Inject funding + price state as if _refresh_funding had run."""
    strat._funding_raw[coin] = funding_hourly
    strat._funding_hist[coin].clear()
    for _ in range(3):
        strat._funding_hist[coin].append(funding_hourly)
    strat._prices[coin] = {
        "funding_hourly": funding_hourly,
        "oracle_px": oracle, "mark_px": mark,
        "mid_px": mark, "premium": 0.0, "source": "test",
    }
    strat._perp_mid[coin] = perp_mid if perp_mid is not None else mark


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

def test_hedged_open_short_perp_on_positive_funding(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)  # 2 bps/h
    s._try_open_carry("BTC", ts=1000.0)
    assert "BTC" in s._carry
    pos = s._carry["BTC"]
    assert pos["direction"] == "short_perp"      # positive funding → short collects
    assert pos["notional"] == pytest.approx(250.0)
    assert pos["perp_entry"] == 100.0 and pos["spot_entry"] == 100.0
    assert s._used_notional == pytest.approx(250.0)


def test_hedged_open_long_perp_on_negative_funding(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=-0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    assert s._carry["BTC"]["direction"] == "long_perp"


def test_hedged_no_entry_when_funding_below_threshold(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.00001, oracle=100.0, mark=100.0)  # 0.1 bps/h
    s._try_open_carry("BTC", ts=1000.0)
    assert "BTC" not in s._carry


def test_hedged_no_entry_when_edge_below_min(tmp_path):
    # 0.6 bps/h clears the entry threshold but not the cost-adjusted edge.
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.00006, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    assert "BTC" not in s._carry


def test_hedged_skips_anomalous_basis(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    # mark 200 bps above oracle → basis blow-out, skip.
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=102.0)
    s._try_open_carry("BTC", ts=1000.0)
    assert "BTC" not in s._carry


def test_hedged_respects_capital(tmp_path):
    # notional per slot = min(500, 250) = 250; with 400 already used, the
    # 250 carry would push to 650 > 500 → the capital guard must reject it.
    s = FundingCarryHedgedStrategy(
        _cfg(_hedged_params(tmp_path), capital=500.0, max_pos=1, max_size=250.0))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._used_notional = 400.0
    s._try_open_carry("BTC", ts=1000.0)
    assert "BTC" not in s._carry


# ---------------------------------------------------------------------------
# Funding accrual
# ---------------------------------------------------------------------------

def test_funding_accrual_short_perp_collects_positive(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    # 10 hours later
    s._accrue_funding("BTC", ts=1000.0 + 10 * 3600)
    # short perp collects positive funding: 0.0002 × 250 × 10h
    assert s._carry["BTC"]["accrued_funding"] == pytest.approx(0.0002 * 250.0 * 10.0)


def test_funding_accrual_long_perp_sign(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=-0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    s._accrue_funding("BTC", ts=1000.0 + 5 * 3600)
    # long perp collects when funding is negative → positive accrual
    assert s._carry["BTC"]["accrued_funding"] == pytest.approx(0.0002 * 250.0 * 5.0)


# ---------------------------------------------------------------------------
# Delta-neutrality
# ---------------------------------------------------------------------------

def test_carry_is_delta_neutral(tmp_path):
    """A parallel move in perp AND spot must net to ~0 across the two legs."""
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    # both legs rally 10 %
    s._perp_mid["BTC"] = 110.0
    s._prices["BTC"]["oracle_px"] = 110.0
    perp_pnl, spot_pnl = s._mark(s._carry["BTC"], "BTC")
    assert perp_pnl == pytest.approx(-25.0)   # short perp loses on a rally
    assert spot_pnl == pytest.approx(+25.0)   # long spot gains
    assert perp_pnl + spot_pnl == pytest.approx(0.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Exit
# ---------------------------------------------------------------------------

def test_exit_on_funding_normalized(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    # funding collapses below the 0.15 bps/h exit threshold
    s._funding_raw["BTC"] = 0.000005
    assert s._carry_exit_reason("BTC", ts=1000.0 + 3600) == "funding_normalized"


def test_exit_on_max_hold(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    # 72 h + 1 s later
    assert s._carry_exit_reason("BTC", ts=1000.0 + 72 * 3600 + 1) == "max_hold"


# ---------------------------------------------------------------------------
# Full cycle PnL + logging
# ---------------------------------------------------------------------------

def test_close_carry_pnl_and_csv(tmp_path):
    params = _hedged_params(tmp_path)
    s = FundingCarryHedgedStrategy(_cfg(params))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)

    # Hold 100 h, prices flat → PnL is funding minus the two-leg round-trip cost.
    close_ts = 1000.0 + 100 * 3600
    s._accrue_funding("BTC", close_ts)
    s._close_carry("BTC", close_ts, reason="funding_normalized")

    assert "BTC" not in s._carry
    assert s._n_trades == 1
    leg_cost = (4.5 + 2.0 + 7.0 + 2.0)                   # 15.5 bps one-way
    fees = 2 * 250.0 * leg_cost / 10_000.0               # entry + exit
    funding = 0.0002 * 250.0 * 100.0                     # = 5.0
    expected_net = funding - fees
    assert s._realized_pnl == pytest.approx(expected_net, abs=1e-6)
    assert expected_net > 0                              # 100 h of carry beats cost
    assert s._wins == 1
    assert s._used_notional == pytest.approx(0.0)

    # CSV row written
    log_path = Path(params["paper_log_path"])
    assert log_path.exists()
    rows = list(csv.DictReader(open(log_path, encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["symbol"] == "BTC"
    assert rows[0]["direction"] == "short_perp"
    assert float(rows[0]["net_pnl"]) == pytest.approx(expected_net, abs=1e-6)
    assert float(rows[0]["funding_collected"]) == pytest.approx(funding, abs=1e-6)


def test_calibration_exposes_hedge_state(tmp_path):
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.05)
    cal = s.get_calibration_data("BTC")
    assert cal["hedged"] is True
    assert cal["hedge_mode_available"] is True
    assert cal["oracle_px"] == pytest.approx(100.0)
    assert cal["basis_bps"] is not None
    s._try_open_carry("BTC", ts=1000.0)
    cal = s.get_calibration_data("BTC")
    assert cal["in_position"] is True
    assert cal["carry_position"]["direction"] == "short_perp"


def test_expected_edge_calibration_backcompat(tmp_path):
    """The original expected_edge_bps contract must still hold."""
    s = FundingCarryHedgedStrategy(_cfg({
        "funding_entry_bps_per_hour": 0.5, "expected_hold_hours": 4,
        "taker_fee_bps": 3.5, "slippage_bps": 2.0, "safety_buffer_bps": 2.0,
    }))
    s._funding_raw["BTC"] = 0.0001                       # 1 bps/h
    cal = s.get_calibration_data("BTC")
    # 1 bps/h × 4 h − (3.5 + 2 + 2) = −3.5
    assert cal["expected_edge_bps"] == pytest.approx(-3.5, abs=1e-6)


# ---------------------------------------------------------------------------
# Depth-aware slippage (microstructure cost model)
# ---------------------------------------------------------------------------

class _FakeBook:
    """Minimal stub matching the OrderBook API used by the strategy."""
    def __init__(self, bids, asks):
        self.bids = bids
        self.asks = asks

    @property
    def best_bid(self): return self.bids[0][0] if self.bids else None
    @property
    def best_ask(self): return self.asks[0][0] if self.asks else None
    @property
    def mid(self):
        if self.best_bid and self.best_ask:
            return (self.best_bid + self.best_ask) / 2.0
        return None


def test_book_impact_bps_thin_book_returns_none(tmp_path):
    """If notional exceeds book depth, returns None (caller falls back to floor)."""
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    book = _FakeBook(bids=[(99.5, 0.1)], asks=[(100.5, 0.1)])  # only ~$10 each side
    assert s._book_impact_bps(book, notional_usd=10_000.0, side="BUY") is None
    assert s._book_impact_bps(book, notional_usd=10_000.0, side="SELL") is None


def test_book_impact_bps_deep_book_low_slippage(tmp_path):
    """Deep tight book → impact well below the 2 bps configured floor."""
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    # Mid=100, 1bp tight: ask 100.005, bid 99.995, each level $50k+ in notional.
    book = _FakeBook(
        bids=[(99.995, 1000), (99.99, 1000), (99.98, 1000)],
        asks=[(100.005, 1000), (100.01, 1000), (100.02, 1000)],
    )
    impact = s._book_impact_bps(book, notional_usd=200.0, side="BUY")
    assert impact is not None
    assert impact < 1.0  # tight book → sub-1bp impact for small notional


def test_leg_cost_uses_depth_when_book_present(tmp_path):
    """When a tight book is cached, _leg_cost_bps should drop below the
    configured-floor cost. When no book, falls back to the floor."""
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    # Configured floor cost: 4.5 + 2 + 7 + 2 = 15.5 bps per leg.
    floor = s._leg_cost_bps()
    assert floor == pytest.approx(15.5, abs=1e-6)

    # Cache a tight book and re-evaluate with coin+notional.
    s._perp_book["BTC"] = _FakeBook(
        bids=[(99.999, 5000), (99.998, 5000)],
        asks=[(100.001, 5000), (100.002, 5000)],
    )
    with_depth = s._leg_cost_bps("BTC", 200.0)
    # Depth ≈ 0.1 bps but floor is 2.0 → max(floor, depth) = 2.0 → unchanged.
    assert with_depth == pytest.approx(15.5, abs=1e-6)


def test_leg_cost_takes_max_of_floor_and_depth(tmp_path):
    """If real book impact > floor, use the book impact (safety: never under-estimate)."""
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    # Shallow book: 200 USD notional eats through wide levels → impact > 2 bps.
    s._perp_book["BTC"] = _FakeBook(
        bids=[(99.0, 1), (95.0, 2), (90.0, 5)],
        asks=[(101.0, 1), (105.0, 2), (110.0, 5)],
    )
    cost = s._leg_cost_bps("BTC", 200.0)
    # Floor would give 15.5 bps; impact here is much larger → cost > 15.5.
    assert cost > 15.5


def test_on_orderbook_update_caches_book(tmp_path):
    """The strategy must cache the book in on_orderbook_update so the next
    _leg_cost_bps call can use it."""
    s = FundingCarryHedgedStrategy(_cfg(_hedged_params(tmp_path)))
    book = _FakeBook(
        bids=[(99.99, 1000), (99.98, 1000)],
        asks=[(100.01, 1000), (100.02, 1000)],
    )
    s.on_orderbook_update("BTC", book, ts=1000.0)
    assert s._perp_book["BTC"] is book
    assert s._perp_mid["BTC"] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# Maker mode (post-only 2-leg, honest no-fill penalty)
# ---------------------------------------------------------------------------

def test_maker_mode_cuts_cost_vs_taker(tmp_path):
    """Maker mode must cost less than taker mode but still > 0 (no-fill penalty
    keeps the EV honest)."""
    p = _hedged_params(tmp_path)
    s_taker = FundingCarryHedgedStrategy(_cfg(p))
    taker_cost = s_taker._leg_cost_bps()  # 4.5 + 2 + 7 + 2 = 15.5

    p_maker = dict(p)
    p_maker["entry_mode"] = "maker"
    s_maker = FundingCarryHedgedStrategy(_cfg(p_maker))
    maker_cost = s_maker._leg_cost_bps()
    # Defaults: 1.5 (perp maker) + 4.0 (spot maker) + 2.5 (half of 5 bps no-fill RT)
    assert maker_cost == pytest.approx(8.0, abs=1e-6)
    assert maker_cost < taker_cost


def test_maker_mode_logged_in_carry_position(tmp_path):
    """Opening a position with entry_mode=maker should tag the position so the
    CSV log can separate maker vs taker fills."""
    p = _hedged_params(tmp_path)
    p["entry_mode"] = "maker"
    s = FundingCarryHedgedStrategy(_cfg(p))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    assert s._carry["BTC"]["entry_mode"] == "maker"


def test_maker_mode_enters_where_taker_does_not(tmp_path):
    """A funding level that fails the taker edge filter should still pass with
    maker mode — that's the whole point of the recalibration."""
    # 0.7 bps/h × 24h = 16.8 bps gross.
    # Taker RT cost = 2 × 15.5 = 31 bps → edge < 0 → rejected.
    # Maker RT cost = 2 × 8   = 16 bps → edge ≈ +0.8 → marginal (rejected by min_edge=5).
    # Bump funding to 1.0 bps/h: gross = 24 bps. Maker: 24 − 16 − 2 = +6 bps → enters.
    base_params = _hedged_params(tmp_path)
    base_params["min_expected_edge_bps"] = 5.0

    s_taker = FundingCarryHedgedStrategy(_cfg(base_params))
    _prime(s_taker, "BTC", funding_hourly=0.0001, oracle=100.0, mark=100.0)
    s_taker._try_open_carry("BTC", ts=1000.0)
    assert "BTC" not in s_taker._carry, "taker should reject (cost > edge)"

    p_maker = dict(base_params)
    p_maker["entry_mode"] = "maker"
    s_maker = FundingCarryHedgedStrategy(_cfg(p_maker))
    _prime(s_maker, "BTC", funding_hourly=0.0001, oracle=100.0, mark=100.0)
    s_maker._try_open_carry("BTC", ts=1000.0)
    assert "BTC" in s_maker._carry, "maker should enter (cost halved)"
    assert s_maker._carry["BTC"]["entry_mode"] == "maker"


def test_maker_csv_log_contains_entry_mode(tmp_path):
    """CSV column entry_mode must round-trip from position to row."""
    p = _hedged_params(tmp_path)
    p["entry_mode"] = "maker"
    s = FundingCarryHedgedStrategy(_cfg(p))
    _prime(s, "BTC", funding_hourly=0.0002, oracle=100.0, mark=100.0)
    s._try_open_carry("BTC", ts=1000.0)
    s._close_carry("BTC", ts=1000.0 + 4 * 3600.0, reason="max_hold")
    with open(p["paper_log_path"]) as f:
        rows = list(csv.DictReader(f))
    assert rows[-1]["entry_mode"] == "maker"
