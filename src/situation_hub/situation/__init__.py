"""Read the latest durable engine evaluation for the CLI and MCP tool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
import sqlite3
from typing import Any

from situation_hub import config
from situation_hub.engine import MAX_TICK_AGE
from situation_hub.signal_log import read_snapshot
from situation_hub.votes import timestamp, utc_iso


def read_situation() -> dict[str, Any]:
    """Return the latest model-safe snapshot or a generic diagnostic."""
    now = datetime.now(UTC)
    signals = []
    as_of = now.replace(microsecond=0)
    try:
        data = config.load()
        path = config.database_path(data)
        signals = data.get("signals", [])
    except config.CONFIG_ERRORS:
        error = config.CONFIG_ERROR
    else:
        try:
            snapshot = read_snapshot(path)
            evaluated = timestamp(snapshot["as_of"]) if snapshot is not None else None
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
            error = "Situation database unavailable"
        else:
            if evaluated is not None and timedelta(0) <= now - evaluated <= MAX_TICK_AGE:
                return snapshot
            if evaluated is not None:
                as_of = evaluated
            error = "engine stale"

    return {
        "presence": "unknown",
        "confidence": None,
        "since": None,
        "activity": "unknown",
        "evidence": [],
        "against": [],
        "stale": [signal["id"] for signal in signals],
        "as_of": utc_iso(as_of),
        "error": error,
    }


def encode_snapshot(snapshot: dict[str, Any]) -> str:
    return json.dumps(snapshot, separators=(",", ":"), allow_nan=False)
