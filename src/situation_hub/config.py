"""Load and validate the private configuration without quoting its values."""

from __future__ import annotations

import math
import os
import re
from pathlib import Path
from typing import Any

import yaml

from situation_hub.votes import STATE_RULES

DEFAULT_PATH = "~/.config/situation-hub/config.yaml"
DEFAULT_PRESENCE = {
    "prior_home": 0.6,
    "home_threshold": 0.8,
    "away_threshold": 0.2,
    "home_hold_minutes": 2,
    "away_hold_minutes": 15,
}
CONFIG_ERROR = "Situation configuration unavailable"
CONFIG_ERRORS = (OSError, TypeError, ValueError, OverflowError, RuntimeError, yaml.YAMLError)
ENTITY_ID = re.compile(r"[a-z0-9_]+\.[a-z0-9_]+")
SLUG = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]*")


def config_path() -> Path:
    return Path(os.environ.get("SITUATION_CONFIG", DEFAULT_PATH)).expanduser()


def database_path(data: dict[str, Any]) -> Path:
    path = data.get("database")
    if not isinstance(path, str) or not path or "\0" in path:
        raise ValueError("Expected a database path")
    path.encode("utf-8")
    return Path(path).expanduser().absolute()


def load(path: Path | None = None) -> dict[str, Any]:
    with open(path or config_path(), encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ValueError("Expected a configuration mapping")
    validate_signals(data.get("signals", []))
    presence_settings(data)
    activity = data.get("activity")
    if activity is not None and not isinstance(activity, dict):
        raise ValueError("Expected an activity mapping")
    asleep = (activity or {}).get("asleep_entity")
    if asleep is not None and (not isinstance(asleep, str) or not ENTITY_ID.fullmatch(asleep)):
        raise ValueError("Expected a sleep entity ID")
    return data


def presence_settings(data: dict[str, Any]) -> dict[str, float]:
    overrides = data.get("presence", {})
    if not isinstance(overrides, dict):
        raise ValueError("Expected a presence settings mapping")
    settings = DEFAULT_PRESENCE | overrides
    home, away = settings["home_threshold"], settings["away_threshold"]
    if type(home) not in (int, float) or type(away) not in (int, float) or not 0 <= away < home <= 1:
        raise ValueError("Expected ordered presence thresholds between zero and one")
    _validate_probability(settings["prior_home"])
    for key in ("home_hold_minutes", "away_hold_minutes"):
        _validate_minutes(settings[key])
    return settings


def _validate_probability(value: Any) -> None:
    if type(value) not in (int, float) or not 0 < value < 1:
        raise ValueError("Expected a probability strictly between zero and one")


def _validate_minutes(value: Any) -> None:
    if type(value) not in (int, float) or value < 0 or not math.isfinite(value * 60):
        raise ValueError("Expected a finite, nonnegative duration")


def validate_signals(signals: list[dict[str, Any]]) -> None:
    if not isinstance(signals, list):
        raise ValueError("Expected a list of signals")
    slugs = set()
    sources = set()
    for signal in signals:
        if not isinstance(signal, dict):
            raise ValueError("Expected a signal mapping")
        slug = signal.get("id")
        source = signal.get("entity_id")
        kind = signal.get("kind")
        domain = signal.get("domain")
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise ValueError("Expected a signal slug")
        if not isinstance(source, str) or not ENTITY_ID.fullmatch(source):
            raise ValueError("Expected a source entity ID")
        if source in sources:
            raise ValueError("Signals must use distinct source entity IDs")
        if slug in slugs:
            raise ValueError("Signals must use distinct IDs")
        if not isinstance(kind, str) or kind not in STATE_RULES:
            raise ValueError("Unsupported signal kind")
        if not isinstance(domain, str) or not SLUG.fullmatch(domain):
            raise ValueError("Expected a signal domain slug")
        if STATE_RULES[kind][1] is None:
            values = signal.get("home_values")
            if not isinstance(values, list) or any(not isinstance(value, str) for value in values):
                raise ValueError("Expected a list of home states")
        _validate_probability(signal.get("prob_given_home"))
        _validate_probability(signal.get("prob_given_away"))
        if "stale_after_minutes" in signal:
            _validate_minutes(signal["stale_after_minutes"])
        slugs.add(slug)
        sources.add(source)
