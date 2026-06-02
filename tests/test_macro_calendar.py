"""
test_macro_calendar.py — fenêtres de blackout macro (NFP/CPI/FOMC/discours).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

from risk.macro_calendar import MacroCalendar, _et_to_utc
_first_friday = MacroCalendar._first_friday


def _write(tmp_path, events):
    p = tmp_path / "ev.json"
    p.write_text(json.dumps({"events": events}), encoding="utf-8")
    return p


def test_et_to_utc_dst():
    # 8:30 ET en été (EDT, -4) = 12:30 UTC ; en hiver (EST, -5) = 13:30 UTC
    summer = _et_to_utc(datetime(2026, 7, 10), 8, 30)
    winter = _et_to_utc(datetime(2026, 1, 9), 8, 30)
    assert (summer.hour, summer.minute) == (12, 30)
    assert (winter.hour, winter.minute) == (13, 30)


def test_first_friday():
    assert _first_friday(2026, 5) == datetime(2026, 5, 1).date()   # 1 mai 2026 = vendredi
    assert _first_friday(2026, 6).weekday() == 4                    # un vendredi


def test_blackout_window_pre_post(tmp_path):
    cal = MacroCalendar(_write(tmp_path, [{"type": "CPI", "date": "2026-07-10"}]),
                        pre_min=15, post_min=15, enable_nfp_rule=False)
    evt = _et_to_utc(datetime(2026, 7, 10), 8, 30)   # 12:30 UTC
    assert cal.is_blackout(evt - timedelta(minutes=10)) is True     # pré
    assert cal.is_blackout(evt + timedelta(minutes=10)) is True     # post
    assert cal.is_blackout(evt - timedelta(minutes=20)) is False    # avant la fenêtre
    assert cal.is_blackout(evt + timedelta(minutes=20)) is False    # après la fenêtre
    st = cal.status(evt - timedelta(minutes=10))
    assert st["event"] == "CPI" and st["phase"] == "pre"


def test_fomc_speech_longer_post(tmp_path):
    cal = MacroCalendar(_write(tmp_path, [{"type": "FOMC_SPEECH", "date": "2026-07-29"}]),
                        pre_min=15, post_min=15, enable_nfp_rule=False)
    evt = _et_to_utc(datetime(2026, 7, 29), 14, 30)
    # post 30 min pour le discours : encore en blackout à +25, plus à +35
    assert cal.is_blackout(evt + timedelta(minutes=25)) is True
    assert cal.is_blackout(evt + timedelta(minutes=35)) is False


def test_nfp_rule_generated():
    cal = MacroCalendar(events_path="___none___", enable_nfp_rule=True, nfp_months_ahead=2)
    # un 1er vendredi à 08:30 ET doit être en blackout
    ff = _first_friday(2026, 7)
    evt = _et_to_utc(datetime(ff.year, ff.month, ff.day), 8, 30)
    # reconstruire le calendrier autour de cette date
    cal.reload(ref=evt)
    assert cal.is_blackout(evt) is True
    assert cal.status(evt)["event"] == "NFP"


def test_status_next_event(tmp_path):
    cal = MacroCalendar(_write(tmp_path, [{"type": "CPI", "date": "2026-07-10"}]),
                        enable_nfp_rule=False)
    now = datetime(2026, 7, 10, 0, 0, tzinfo=timezone.utc)
    st = cal.status(now)
    assert st["next_event"] == "CPI"
    assert st["seconds_to_next"] > 0 and st["in_blackout"] is False
