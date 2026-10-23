"""Alarm data model for Voice Alarms."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any


def parse_time(text: str) -> tuple[int, int]:
    """Parse a human-readable time string into (hour, minute).

    Supported formats (case-insensitive AM/PM):
      '7'        -> (7, 0)   bare integer treated as 24-hour hour at :00
      '7 AM'     -> (7, 0)
      '12 AM'    -> (0, 0)   midnight
      '12 PM'    -> (12, 0)  noon
      '6:30 pm'  -> (18, 30)
      '19:00'    -> (19, 0)

    Raises ValueError for invalid input, out-of-range values, or
    impossible AM/PM combinations (e.g. '13 PM', '0 AM').
    """
    raw = text.strip()
    upper = raw.upper()

    am_pm: str | None = None
    if upper.endswith("AM"):
        am_pm = "AM"
        raw = raw[:-2].strip()
    elif upper.endswith("PM"):
        am_pm = "PM"
        raw = raw[:-2].strip()

    if ":" in raw:
        h_part, m_part = raw.split(":", 1)
        try:
            h = int(h_part.strip())
            m = int(m_part.strip())
        except ValueError:
            raise ValueError(f"Invalid time format: {text!r}") from None
    else:
        try:
            h = int(raw.strip())
            m = 0
        except ValueError:
            raise ValueError(f"Invalid time format: {text!r}") from None

    if am_pm == "AM":
        if h == 12:
            h = 0  # 12 AM = midnight
        elif not (1 <= h <= 11):
            raise ValueError(
                f"Hour {h} is out of range for AM time (use 1-12 or 12-hour clock)"
            )
    elif am_pm == "PM":
        if h == 12:
            pass  # 12 PM = noon, keep as 12
        elif 1 <= h <= 11:
            h += 12
        else:
            raise ValueError(
                f"Hour {h} is out of range for PM time (use 1-12 or 12-hour clock)"
            )

    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise ValueError(f"Time out of range: {h:02d}:{m:02d}")

    return (h, m)


@dataclass
class Alarm:
    """A single scheduled alarm."""

    id: str
    satellite: str
    hour: int
    minute: int
    repeat: str  # once | daily | weekdays | weekends
    once_at: float | None
    last_fired: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict."""
        return {
            "id": self.id,
            "satellite": self.satellite,
            "hour": self.hour,
            "minute": self.minute,
            "repeat": self.repeat,
            "once_at": self.once_at,
            "last_fired": self.last_fired,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Alarm:
        """Deserialize from a dict."""
        return cls(
            id=data["id"],
            satellite=data["satellite"],
            hour=int(data["hour"]),
            minute=int(data["minute"]),
            repeat=data["repeat"],
            once_at=data.get("once_at"),
            last_fired=data.get("last_fired", ""),
        )

    def due(self, now: datetime) -> bool:
        """Return True if this alarm should fire at the given local datetime.

        Checks the current local minute against hour/minute, then applies the
        repeat rule. Returns False if the alarm already fired this minute
        (last_fired equals now.strftime('%Y-%m-%dT%H:%M')).
        """
        if now.hour != self.hour or now.minute != self.minute:
            return False

        if self.last_fired == now.strftime("%Y-%m-%dT%H:%M"):
            return False

        if self.repeat == "once":
            if self.once_at is None:
                return False
            if now.tzinfo is not None:
                once_date = datetime.fromtimestamp(self.once_at, tz=now.tzinfo).date()
            else:
                once_date = datetime.fromtimestamp(self.once_at).date()
            return now.date() == once_date

        if self.repeat == "daily":
            return True

        if self.repeat == "weekdays":
            return now.weekday() < 5  # Monday=0 ... Friday=4

        if self.repeat == "weekends":
            return now.weekday() >= 5  # Saturday=5, Sunday=6

        return False


def new_alarm(
    satellite: str,
    text: str,
    repeat: str,
    now: datetime,
) -> Alarm:
    """Create a new Alarm from a natural-language time string.

    For repeat='once', schedules for today if the parsed time is still in the
    future, otherwise schedules for tomorrow.  All other repeat modes ignore
    the date component of 'now'.
    """
    h, m = parse_time(text)

    once_at: float | None = None
    if repeat == "once":
        target = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        once_at = target.timestamp()

    return Alarm(
        id=str(uuid.uuid4()),
        satellite=satellite,
        hour=h,
        minute=m,
        repeat=repeat,
        once_at=once_at,
    )
