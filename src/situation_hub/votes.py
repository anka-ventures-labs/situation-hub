"""Classify and timestamp one Home Assistant reading."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

DEFAULT_STALE_MINUTES = 20
NO_READING = ("", "unknown", "unavailable")
TIMESTAMP_FIELDS = ("last_reported", "last_updated", "last_changed")

# None for away means any other usable state.
STATE_RULES = {
    "wifi": ((), None),
    "gps_zone": ((), None),
    "media_playing": (("playing",), ()),
    "asleep": (("on",), ()),
    "device_active": (("on",), ()),
    "ping": (("on",), ("off",)),
}


@dataclass(frozen=True)
class Vote:
    says: str
    reported: datetime
    age_minutes: int


def timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def utc_iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def raw_state(reading: dict[str, Any] | None) -> str | None:
    raw = reading.get("state") if reading is not None else None
    if not isinstance(raw, str):
        return None
    try:
        raw.encode("utf-8")
    except UnicodeEncodeError:
        return None
    return raw


def within_freshness(signal: dict[str, Any], reported: datetime, at: datetime) -> bool:
    age_seconds = (at - reported).total_seconds()
    return 0 <= age_seconds <= signal.get("stale_after_minutes", DEFAULT_STALE_MINUTES) * 60


def fresh_vote(
    signal: dict[str, Any], reading: dict[str, Any] | None, now: datetime
) -> Vote | None:
    raw = raw_state(reading)
    if raw is None or raw in NO_READING:
        return None

    reported = next(
        (
            parsed
            for field in TIMESTAMP_FIELDS
            if (parsed := timestamp(reading.get(field))) is not None
        ),
        None,
    )
    if reported is None or not within_freshness(signal, reported, now):
        return None

    home, away = STATE_RULES[signal["kind"]]
    if away is None:
        home = signal["home_values"]
    age_minutes = int((now - reported).total_seconds() / 60)
    if raw in home:
        return Vote("home", reported, age_minutes)
    if away is None or raw in away:
        return Vote("away", reported, age_minutes)
    return None
