"""
macro_calendar.py — calendrier des gros événements macro + fenêtres de blackout.

But : autour des annonces à fort impact (NFP, US CPI, décision de taux FOMC,
conférence/discours du président de la Fed), mettre les stratégies en STAND-BY :
le moteur LIQUIDE les positions (STOP) et GÈLE les nouvelles entrées (FREEZE)
pendant [event − pre_min, event + post_min]. Le discours Fed a un post plus long.

Heures officielles (US/Eastern, converties en UTC en gérant le DST) :
  NFP   08:30 ET · CPI 08:30 ET · FOMC (taux) 14:00 ET · Discours Fed 14:30 ET.

Sources des dates :
  - NFP : règle « 1er vendredi du mois » → généré automatiquement.
  - CPI / FOMC / discours : dates explicites dans config/macro_events.json
    (à tenir à jour depuis le calendrier officiel BLS / federalreserve.gov).

Lecture seule : ce module ne fait que dire si on est en blackout ; c'est le moteur
qui exécute le STOP/FREEZE.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

try:
    from zoneinfo import ZoneInfo
    _ET = ZoneInfo("America/New_York")
except Exception:                       # pragma: no cover - fallback sans tzdata
    _ET = None

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVENTS = ROOT / "config" / "macro_events.json"

# Heure par défaut (ET) + post-fenêtre spécifique par type.
_DEFAULT_ET_TIME = {"NFP": (8, 30), "CPI": (8, 30), "FOMC": (14, 0), "FOMC_SPEECH": (14, 30)}
_DEFAULT_POST_MIN = {"FOMC_SPEECH": 30}   # discours : post 30 min ; sinon post générique


def _us_eastern_offset_hours(d: datetime) -> int:
    """Offset ET→UTC en heures (EDT=-4, EST=-5). Fallback si zoneinfo absent :
    DST US = 2e dimanche de mars → 1er dimanche de novembre."""
    def _nth_weekday(year, month, weekday, n):
        dt = datetime(year, month, 1)
        add = (weekday - dt.weekday()) % 7 + 7 * (n - 1)
        return (dt + timedelta(days=add)).date()
    y = d.year
    dst_start = _nth_weekday(y, 3, 6, 2)   # 2e dimanche mars (dimanche=6)
    dst_end = _nth_weekday(y, 11, 6, 1)    # 1er dimanche novembre
    return 4 if dst_start <= d.date() < dst_end else 5


def _et_to_utc(d: datetime, hour: int, minute: int) -> datetime:
    """datetime UTC pour `hour:minute` heure de l'Est, le jour `d`."""
    if _ET is not None:
        local = datetime(d.year, d.month, d.day, hour, minute, tzinfo=_ET)
        return local.astimezone(timezone.utc)
    off = _us_eastern_offset_hours(d)
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=timezone.utc) + timedelta(hours=off)


@dataclass
class _Event:
    etype: str
    when_utc: datetime
    pre_min: int
    post_min: int

    @property
    def start(self) -> datetime:
        return self.when_utc - timedelta(minutes=self.pre_min)

    @property
    def end(self) -> datetime:
        return self.when_utc + timedelta(minutes=self.post_min)


class MacroCalendar:
    def __init__(self, events_path: Optional[Path] = None,
                 pre_min: int = 15, post_min: int = 15,
                 nfp_months_ahead: int = 3, enable_nfp_rule: bool = True):
        self.events_path = Path(events_path or DEFAULT_EVENTS)
        self.pre_min = int(pre_min)
        self.post_min = int(post_min)
        self.nfp_months_ahead = int(nfp_months_ahead)
        self.enable_nfp_rule = bool(enable_nfp_rule)
        self._events: list[_Event] = []
        self.reload()

    # ── construction des événements ──────────────────────────────────────

    def _post_for(self, etype: str) -> int:
        return int(_DEFAULT_POST_MIN.get(etype, self.post_min))

    @staticmethod
    def _first_friday(year: int, month: int):
        dt = datetime(year, month, 1)
        return (dt + timedelta(days=(4 - dt.weekday()) % 7)).date()  # vendredi=4

    def _nfp_events(self, ref: datetime) -> list[_Event]:
        out = []
        y, m = ref.year, ref.month
        for _ in range(self.nfp_months_ahead + 1):
            d = self._first_friday(y, m)
            h, mi = _DEFAULT_ET_TIME["NFP"]
            out.append(_Event("NFP", _et_to_utc(datetime(d.year, d.month, d.day), h, mi),
                              self.pre_min, self._post_for("NFP")))
            m += 1
            if m > 12:
                m = 1; y += 1
        return out

    def reload(self, ref: Optional[datetime] = None) -> None:
        ref = ref or datetime.now(timezone.utc)
        events: list[_Event] = []
        # 1) dates explicites (CPI / FOMC / discours / NFP override)
        if self.events_path.exists():
            try:
                data = json.loads(self.events_path.read_text(encoding="utf-8"))
                for e in data.get("events", []):
                    etype = str(e["type"]).upper()
                    date = datetime.strptime(e["date"], "%Y-%m-%d")
                    if "time_et" in e:
                        h, mi = [int(x) for x in e["time_et"].split(":")]
                    else:
                        h, mi = _DEFAULT_ET_TIME.get(etype, (8, 30))
                    when = _et_to_utc(date, h, mi)
                    events.append(_Event(etype, when, int(e.get("pre_min", self.pre_min)),
                                         int(e.get("post_min", self._post_for(etype)))))
            except Exception:
                pass
        # 2) NFP par règle
        if self.enable_nfp_rule:
            events.extend(self._nfp_events(ref))
        events.sort(key=lambda x: x.when_utc)
        self._events = events

    # ── interrogation ────────────────────────────────────────────────────

    def status(self, now: Optional[datetime] = None) -> dict:
        now = now or datetime.now(timezone.utc)
        active = next((e for e in self._events if e.start <= now <= e.end), None)
        upcoming = [e for e in self._events if e.when_utc >= now]
        nxt = upcoming[0] if upcoming else None
        return {
            "in_blackout": active is not None,
            "event": active.etype if active else None,
            "phase": (None if active is None else
                      ("pre" if now < active.when_utc else "post")),
            "blackout_until": active.end.isoformat() if active else None,
            "next_event": nxt.etype if nxt else None,
            "next_event_utc": nxt.when_utc.isoformat() if nxt else None,
            "seconds_to_next": (int((nxt.when_utc - now).total_seconds()) if nxt else None),
            "seconds_to_blackout": (int((nxt.start - now).total_seconds())
                                    if nxt and nxt.start > now else None),
        }

    def is_blackout(self, now: Optional[datetime] = None) -> bool:
        return self.status(now)["in_blackout"]
