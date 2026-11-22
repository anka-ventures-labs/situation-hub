from contextlib import closing
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import sqlite3

import httpx
import pytest
import yaml

from situation_hub import situation
from situation_hub.cli import main
from situation_hub.config import load
from situation_hub.signal_log import open_database
from situation_hub.tick import tick

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 22, 12, 13, 5, 123456, tzinfo=UTC)


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW.astimezone(tz)


@pytest.fixture
def configured_db(monkeypatch, tmp_path):
    monkeypatch.setattr(situation, "datetime", FixedDateTime)
    monkeypatch.delenv("HASS_TOKEN", raising=False)
    database = tmp_path / "db" / "situation.db"
    config = load(ROOT / "config.example.yaml")
    config["database"] = str(database)
    config["presence"]["home_hold_minutes"] = 0
    config_path = tmp_path / "example.yaml"
    config_path.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("SITUATION_CONFIG", str(config_path))
    return config, database


def write_home_tick(config, database, at=NOW):
    wifi, *_ = config["signals"]
    sample = {
        "entity_id": wifi["entity_id"],
        "state": "ExampleWiFi",
        "last_reported": (at - timedelta(minutes=1)).isoformat(),
    }

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[sample])
        return httpx.Response(201)

    with (
        open_database(database) as db,
        httpx.Client(base_url="http://ha.example", transport=httpx.MockTransport(handler)) as ha,
    ):
        assert tick(ha, db, config, at=at) == (True, True)


def test_reader_serves_persisted_snapshot_without_ha_or_token(configured_db, monkeypatch, capsys):
    config, database = configured_db
    write_home_tick(config, database)

    def no_network(*args, **kwargs):
        pytest.fail("The reader tried to open an HTTP client")

    monkeypatch.setattr(httpx, "Client", no_network)
    result = situation.read_situation()
    assert result == {
        "presence": "home",
        "confidence": pytest.approx(0.6 * 0.9 / (0.6 * 0.9 + 0.4 * 0.08)),
        "since": "2026-11-22T12:13:05.123456Z",
        "activity": "home_idle",
        "evidence": [{"signal": "phone_wifi", "says": "home", "age_min": 1}],
        "against": [],
        "stale": ["living_room_tv", "phone_gps", "phone_ping", "asleep"],
        "as_of": "2026-11-22T12:13:05.123456Z",
    }
    assert main(["situation"]) == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == result
    assert output.err == ""
    for value in ("ExampleWiFi", "sensor.example_phone_ssid", str(database)):
        assert value not in output.out


@pytest.mark.parametrize(("age", "expected"), [
    (timedelta(minutes=3), "home"),
    (timedelta(minutes=3, microseconds=1), "unknown"),
    (-timedelta(microseconds=1), "unknown"),
])
def test_reader_freshness_uses_exact_tick_time(configured_db, age, expected):
    config, database = configured_db
    write_home_tick(config, database, at=NOW - age)
    result = situation.read_situation()
    assert result["presence"] == expected
    if expected == "unknown":
        assert result["error"] == "engine stale"
        assert result["confidence"] is result["since"] is None
        assert result["activity"] == "unknown"
        assert result["evidence"] == result["against"] == []
    else:
        assert "error" not in result


@pytest.mark.parametrize("legacy", [False, True])
def test_missing_evaluation_is_stale_without_modifying_database(configured_db, legacy):
    _, database = configured_db
    if legacy:
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as db:
            db.execute("CREATE TABLE signal_sample (ts TEXT)")
    result = situation.read_situation()
    assert result["error"] == "engine stale"
    assert result["presence"] == "unknown"
    assert database.exists() == legacy
    if legacy:
        with closing(sqlite3.connect(database)) as db:
            assert db.execute("SELECT name FROM sqlite_schema").fetchall() == [("signal_sample",)]


def test_invalid_config_is_generic(configured_db, monkeypatch, tmp_path, capsys):
    path = tmp_path / "invalid.yaml"
    path.write_text("example: [example-private-value")
    monkeypatch.setenv("SITUATION_CONFIG", str(path))
    assert main(["situation"]) == 0
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"] == "Situation configuration unavailable"
    assert captured.err == ""
    assert "example-private-value" not in captured.out
