"""Persist tick results and signal samples in a private SQLite database."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
from typing import Any

from situation_hub.engine import EngineState, Evaluation, Hold
from situation_hub.votes import utc_iso

RETENTION = timedelta(days=30)
SCHEMA = """
CREATE TABLE IF NOT EXISTS signal_sample (
    ts TEXT NOT NULL,
    signal_id TEXT NOT NULL,
    says TEXT NOT NULL,
    raw TEXT,
    PRIMARY KEY (ts, signal_id)
);
CREATE TABLE IF NOT EXISTS presence_change (
    ts TEXT PRIMARY KEY,
    presence TEXT NOT NULL,
    probability REAL NOT NULL,
    evidence_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS engine_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_json TEXT NOT NULL,
    probability REAL NOT NULL,
    activity_since TEXT NOT NULL,
    hold_json TEXT
);
"""


def _secure_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        try:
            path.with_name(path.name + suffix).chmod(0o600)
        except FileNotFoundError:
            pass  # SQLite can create or remove these as connections open and close.


@contextmanager
def open_database(path: Path) -> Iterator[sqlite3.Connection]:
    """Create and open the private database and its WAL under a restrictive umask."""
    previous_umask = os.umask(0o077)
    try:
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        path.parent.chmod(0o700)
        _secure_sidecars(path)
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with closing(sqlite3.connect(path, timeout=30)) as db:
            db.execute("PRAGMA journal_mode = WAL").fetchone()
            db.executescript(SCHEMA)
            _secure_sidecars(path)
            yield db
    finally:
        os.umask(previous_umask)


def _latest_row(db: sqlite3.Connection) -> tuple[str, str, str | None] | None:
    return db.execute(
        "SELECT snapshot_json, activity_since, hold_json FROM engine_state WHERE id = 1"
    ).fetchone()


def read_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with closing(sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)) as db:
        # Existing logger databases start without an evaluation.
        table = db.execute(
            "SELECT 1 FROM sqlite_schema WHERE type = 'table' AND name = 'engine_state'"
        ).fetchone()
        row = _latest_row(db) if table else None
    return json.loads(row[0]) if row else None


def load_state(db: sqlite3.Connection) -> EngineState | None:
    row = _latest_row(db)
    if row is None:
        return None
    snapshot_json, activity_since, hold_json = row
    snapshot = json.loads(snapshot_json)
    hold = None
    if hold_json is not None:
        saved = json.loads(hold_json)
        hold = Hold(
            saved["direction"],
            datetime.fromisoformat(saved["since"]),
            {slug: datetime.fromisoformat(report) for slug, report in saved["reports"].items()},
        )
    return EngineState(
        datetime.fromisoformat(snapshot["as_of"]),
        snapshot["presence"],
        datetime.fromisoformat(snapshot["since"]),
        snapshot["activity"],
        datetime.fromisoformat(activity_since),
        hold,
    )


def _log_time(moment: datetime) -> str:
    # Keep the existing log's fixed-width timestamp format for retention and deduplication.
    return moment.isoformat(timespec="microseconds")


def save_evaluation(db: sqlite3.Connection, result: Evaluation, previous: EngineState | None) -> None:
    state = result.state
    minute = _log_time(state.at.replace(second=0, microsecond=0))
    db.executemany(
        "INSERT OR IGNORE INTO signal_sample (ts, signal_id, says, raw) VALUES (?, ?, ?, ?)",
        ((minute, slug, says, raw) for slug, says, raw in result.samples),
    )
    if previous is None or previous.presence != state.presence:
        evidence = sorted(slug for slug, says, _ in result.samples if says != "none")
        db.execute(
            "INSERT OR IGNORE INTO presence_change (ts, presence, probability, evidence_json) "
            "VALUES (?, ?, ?, ?)",
            (_log_time(state.at), state.presence, result.probability, json.dumps(evidence)),
        )
    hold = state.hold
    hold_json = None
    if hold is not None:
        hold_json = json.dumps({
            "direction": hold.direction,
            "since": utc_iso(hold.since),
            "reports": {slug: utc_iso(report) for slug, report in hold.reports.items()},
        })
    db.execute(
        "INSERT INTO engine_state (id, snapshot_json, probability, activity_since, hold_json) "
        "VALUES (1, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET "
        "snapshot_json = excluded.snapshot_json, probability = excluded.probability, "
        "activity_since = excluded.activity_since, "
        "hold_json = excluded.hold_json",
        (
            json.dumps(result.snapshot, allow_nan=False),
            result.probability,
            utc_iso(state.activity_since),
            hold_json,
        ),
    )
    cutoff = _log_time(state.at - RETENTION)
    db.execute("DELETE FROM signal_sample WHERE ts < ?", (cutoff,))
    db.execute("DELETE FROM presence_change WHERE ts < ?", (cutoff,))
