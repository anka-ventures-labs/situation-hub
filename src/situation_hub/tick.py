"""Sample, evaluate, persist, and publish one Home Assistant tick."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import os
import sqlite3
import sys
from typing import Any

import httpx

from situation_hub import config
from situation_hub.cli import COMMANDS
from situation_hub.engine import ACTIVITY_ENTITY, PRESENCE_ENTITY, Evaluation, evaluate
from situation_hub.signal_log import load_state, open_database, save_evaluation
from situation_hub.votes import utc_iso


def _fetch_states(client: httpx.Client, sources: set[str]) -> dict[str, dict[str, Any]]:
    response = client.get("/api/states")
    response.raise_for_status()
    states = response.json()
    if not isinstance(states, list):
        raise ValueError("Expected a Home Assistant state list")
    snapshot = {}
    for state in states:
        if not isinstance(state, dict):
            continue
        entity_id = state.get("entity_id")
        if isinstance(entity_id, str) and entity_id in sources:
            if entity_id in snapshot:
                raise ValueError("Duplicate entity in Home Assistant state list")
            snapshot[entity_id] = state
    return snapshot


def _publish(client: httpx.Client, result: Evaluation) -> bool:
    state = result.state
    snapshot = result.snapshot
    attributes = {
        "probability": result.probability,
        "as_of": snapshot["as_of"],
        "evidence": [vote["signal"] for vote in snapshot["evidence"]],
        "against": [vote["signal"] for vote in snapshot["against"]],
    }
    successful = True
    for entity, value, since in (
        (PRESENCE_ENTITY, state.presence, state.since),
        (ACTIVITY_ENTITY, state.activity, state.activity_since),
    ):
        try:
            response = client.post(
                f"/api/states/{entity}",
                json={"state": value, "attributes": {**attributes, "since": utc_iso(since)}},
            )
            response.raise_for_status()
        except httpx.HTTPError:
            successful = False
    return successful


def tick(
    client: httpx.Client,
    db: sqlite3.Connection,
    data: dict[str, Any],
    *,
    at: datetime | None = None,
) -> tuple[bool, bool]:
    """Return whether the snapshot was read and both HA states were published."""
    sources = {signal["entity_id"] for signal in data.get("signals", [])}
    asleep = (data.get("activity") or {}).get("asleep_entity")
    if asleep:
        sources.add(asleep)

    with db:
        # Serialize sampling and hold updates, including when a manual tick overlaps launchd.
        db.execute("BEGIN IMMEDIATE")
        previous = load_state(db)
        error = None
        try:
            snapshot = _fetch_states(client, sources)
        except httpx.HTTPError:
            snapshot = {}
            error = "Home Assistant snapshot unavailable"
        except (ValueError, RecursionError):
            snapshot = {}
            error = "Home Assistant snapshot invalid"
        result = evaluate(data, snapshot, previous, at=at or datetime.now(UTC), error=error)
        save_evaluation(db, result, previous)

    with db:
        # Persist before publishing. Skip a superseded evaluation and keep the two
        # POSTs ordered relative to other ticks.
        db.execute("BEGIN IMMEDIATE")
        latest = load_state(db)
        published = latest != result.state or _publish(client, result)
    return error is None, published


def _configure(parser: argparse.ArgumentParser) -> None:
    """The tick command takes no arguments."""


def _run(args: argparse.Namespace) -> int:
    try:
        data = config.load()
        path = config.database_path(data)
        ha = data["home_assistant"]
        url = httpx.URL(ha["url"])
        token_env = ha["token_env"]
        if (
            url.scheme not in ("http", "https")
            or not url.host
            or not isinstance(token_env, str)
            or not token_env
        ):
            raise ValueError("Invalid Home Assistant settings")
        token = os.environ.get(token_env)
    except (*config.CONFIG_ERRORS, KeyError, httpx.InvalidURL):
        print(config.CONFIG_ERROR, file=sys.stderr)
        return 1

    if not token:
        print("Home Assistant token unavailable", file=sys.stderr)
        return 1

    try:
        with (
            open_database(path) as db,
            httpx.Client(
                base_url=url,
                headers={"Authorization": f"Bearer {token}"},
                timeout=5,
                trust_env=False,
            ) as client,
        ):
            read, published = tick(client, db, data)
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError):
        print("Situation tick failed", file=sys.stderr)
        return 1

    if not read:
        print("Could not read Home Assistant snapshot", file=sys.stderr)
    if not published:
        print("Could not publish both Home Assistant states", file=sys.stderr)
    return 0 if read and published else 1


COMMANDS["tick"] = ("Sample and publish the current situation", _configure, _run)
