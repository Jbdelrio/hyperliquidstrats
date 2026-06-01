"""
test_phase5_reversal_strategies.py — PHASE 5 : classes de réversion (structure).

Teste les méthodes de signal statiques (déterministes) et un smoke d'instanciation
+ on_bar_minute sur barres synthétiques (tf=1 pour warmup court). Les classes sont
NO-GO/désactivées ; ces tests garantissent juste qu'elles tournent sans erreur.
"""
from __future__ import annotations

from strategies.base_strategy import BarData, StrategyConfig
from strategies.cross_sectional_reversal import CrossSectionalReversalStrategy as XS
from strategies.liquidation_cascade_reversal import LiquidationCascadeReversalStrategy as LC
from strategies.residual_btc_reversion import ResidualBTCReversionStrategy as RB


def _bar(sym, ts, o, h, l, c, v=1e6):
    return BarData(symbol=sym, ts=float(ts), open=o, high=h, low=l, close=c,
                   volume_usd=v, return_1m=0.0)


# ── méthodes de signal statiques ─────────────────────────────────────────────

def test_xs_rank_signal():
    allr = [-0.05, -0.02, 0.0, 0.02, 0.05]
    assert XS.rank_signal(0.05, allr, 0.25) == -1     # top quartile → short
    assert XS.rank_signal(-0.05, allr, 0.25) == 1     # bottom → long
    assert XS.rank_signal(0.0, allr, 0.25) == 0       # milieu → rien


def test_cascade_signal_fires_on_big_volume_bar():
    n = 30
    opens = [100.0] * n; highs = [100.5] * n; lows = [99.5] * n
    closes = [100.0] * n; vols = [1000.0] * n
    # dernière barre : range énorme + spike volume + rouge → +1 (fade long)
    opens[-1], closes[-1] = 100.0, 96.0
    highs[-1], lows[-1] = 101.0, 95.0
    vols[-1] = 10000.0
    assert LC.cascade_signal(opens, highs, lows, closes, vols, k=3.0, m=3.0, atr_n=14, vw=20) == 1
    # barre calme → 0
    assert LC.cascade_signal(opens[:-1] + [100.0], highs[:-1] + [100.2],
                             lows[:-1] + [99.8], closes[:-1] + [100.0],
                             vols[:-1] + [1000.0], 3.0, 3.0, 14, 20) == 0


def test_residual_z_needs_enough_data():
    short = [100.0 + i * 0.1 for i in range(10)]
    assert RB.residual_z(short, short, bw=120, zw=48) is None
    long_alt = [100.0 * (1.001 ** i) for i in range(200)]
    long_btc = [100.0 * (1.0008 ** i) for i in range(200)]
    z = RB.residual_z(long_alt, long_btc, bw=120, zw=48)
    assert z is None or isinstance(z, float)


# ── smoke instanciation + on_bar_minute ──────────────────────────────────────

def test_xs_strategy_runs():
    cfg = StrategyConfig(name="XS", coins=["A", "B", "C", "D"], max_positions=2,
                         max_position_size_usd=250,
                         params={"tf_minutes": 1, "lookback_bars": 2, "horizon_bars": 3,
                                 "min_universe": 4, "notional_usd": 250})
    s = XS(cfg)
    for t in range(20):
        for sym, drift in [("A", 1.02), ("B", 1.0), ("C", 0.98), ("D", 1.0)]:
            px = 100 * (drift ** t)
            s.on_bar_minute(sym, _bar(sym, t * 60, px, px, px, px), t * 60)
    assert isinstance(s.get_calibration_data("A"), dict)


def test_cascade_strategy_runs():
    cfg = StrategyConfig(name="LC", coins=["X"], max_positions=1, max_position_size_usd=250,
                         params={"tf_minutes": 1, "atr_period": 5, "vol_window": 5,
                                 "horizon_bars": 2, "notional_usd": 250})
    s = LC(cfg)
    out = None
    for t in range(15):
        v = 1e6 if t < 14 else 1e7
        c = 100.0 if t < 14 else 96.0
        out = s.on_bar_minute("X", _bar("X", t * 60, 100, 101 if t == 14 else 100.3,
                                        95 if t == 14 else 99.7, c, v), t * 60) or out
    assert isinstance(s.get_calibration_data("X"), dict)


def test_residual_strategy_runs():
    cfg = StrategyConfig(name="RB", coins=["BTC", "ALT"], max_positions=1,
                         max_position_size_usd=250,
                         params={"tf_minutes": 1, "beta_window": 10, "z_window": 5,
                                 "z_entry": 1.5, "horizon_bars": 3, "notional_usd": 250})
    s = RB(cfg)
    for t in range(40):
        b = 100 * (1.001 ** t); a = 100 * (1.0009 ** t) * (1 + 0.001 * ((-1) ** t))
        s.on_bar_minute("BTC", _bar("BTC", t * 60, b, b, b, b), t * 60)
        s.on_bar_minute("ALT", _bar("ALT", t * 60, a, a, a, a), t * 60)
    assert isinstance(s.get_calibration_data("ALT"), dict)
