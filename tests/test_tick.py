from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone
import json
from pathlib import Path
import plistlib
import sqlite3
import stat

import httpx
import pytest
import yaml

from situation_hub.cli import main
from situation_hub.config import load
from situation_hub.engine import ACTIVITY_ENTITY, PRESENCE_ENTITY
from situation_hub.signal_log import load_state, open_database
from situation_hub.tick import tick

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 11, 22, 10, 40, tzinfo=UTC)


@pytest.fixture
def config():
    return load(ROOT / "config.example.yaml")


def reading(signal, raw, at=AT):
    return {"entity_id": signal["entity_id"], "state": raw, "last_reported": at.isoformat()}


def client(handler):
    return httpx.Client(base_url="http://ha.example", transport=httpx.MockTransport(handler))


def test_tick_persists_holds_across_invocations_and_publishes_safe_attributes(config, tmp_path):
    path = tmp_path / "private" / "situation.db"
    wifi, tv, *_ = config["signals"]
    requests = []
    now = AT

    def handler(request):
        requests.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json=[
                reading(wifi, "ExampleWiFi"),
                reading(tv, "off" if now < AT + timedelta(minutes=3) else "playing", now),
                {"entity_id": "sensor.example_unrelated", "state": "example-unrelated"},
            ])
        payload = json.loads(request.content)
        with closing(sqlite3.connect(path)) as reader:
            saved = json.loads(reader.execute("SELECT snapshot_json FROM engine_state").fetchone()[0])
        key = "presence" if request.url.path.endswith(PRESENCE_ENTITY) else "activity"
        assert payload["state"] == saved[key]
        assert payload["attributes"]["as_of"] == saved["as_of"]
        assert payload["attributes"]["since"].endswith("Z")
        assert payload["attributes"]["evidence"] == [vote["signal"] for vote in saved["evidence"]]
        assert payload["attributes"]["probability"] >= 0.8
        assert "ExampleWiFi" not in request.content.decode()
        assert all(signal["entity_id"] not in request.content.decode() for signal in config["signals"])
        return httpx.Response(201, json=payload)

    for minute in range(4):
        now = AT + timedelta(minutes=minute)
        with client(handler) as ha, open_database(path) as db:
            assert tick(ha, db, config, at=now) == (True, True)
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
            for file in (path, Path(f"{path}-wal"), Path(f"{path}-shm")):
                assert stat.S_IMODE(file.stat().st_mode) == 0o600
            state = load_state(db)
            assert state.presence == ("home" if minute >= 2 else "unknown")
            assert state.hold.since == AT
            if minute == 3:
                assert state.activity == "home_active"
                assert state.activity_since == now
                assert state.since == now - timedelta(minutes=1)

    endpoints = [("GET", "/api/states"), ("POST", f"/api/states/{PRESENCE_ENTITY}"),
                 ("POST", f"/api/states/{ACTIVITY_ENTITY}")]
    assert requests == endpoints * 4
    with closing(sqlite3.connect(path)) as db:
        changes = db.execute("SELECT presence, evidence_json FROM presence_change ORDER BY ts").fetchall()
        assert changes == [("unknown", '["phone_wifi"]'), ("home", '["phone_wifi"]')]
        assert db.execute("SELECT COUNT(*) FROM signal_sample").fetchone() == (4 * len(config["signals"]),)


def test_tick_discards_malformed_unicode_per_state_and_keeps_batch(config, tmp_path):
    wifi, tv, *_ = config["signals"]
    malformed = reading(tv, "\ud800")
    payload = json.dumps([malformed, reading(wifi, "ExampleWiFi")]).encode("ascii")

    def handler(request):
        return httpx.Response(200, content=payload) if request.method == "GET" else httpx.Response(201)

    with client(handler) as ha, open_database(tmp_path / "situation.db") as db:
        assert tick(ha, db, config, at=AT) == (True, True)
        assert db.execute(
            "SELECT signal_id, says, raw FROM signal_sample WHERE signal_id IN (?, ?) ORDER BY signal_id",
            (wifi["id"], tv["id"]),
        ).fetchall() == [("living_room_tv", "none", None), ("phone_wifi", "home", "ExampleWiFi")]


@pytest.mark.parametrize("broken", ["http", "body", "duplicate"])
def test_invalid_snapshot_persists_unknown_and_resets_hold(config, tmp_path, broken):
    wifi, *_ = config["signals"]
    error = False

    def handler(request):
        if request.method == "POST":
            if error:
                assert json.loads(request.content)["state"] == "unknown"
            return httpx.Response(201)
        state = reading(wifi, "ExampleWiFi")
        if error:
            return {
                "http": httpx.Response(503),
                "body": httpx.Response(200, content=b"{"),
                "duplicate": httpx.Response(200, json=[state, state]),
            }[broken]
        return httpx.Response(200, json=[state])

    with client(handler) as ha, open_database(tmp_path / "situation.db") as db:
        assert tick(ha, db, config, at=AT) == (True, True)
        assert tick(ha, db, config, at=AT + timedelta(minutes=2)) == (True, True)
        assert load_state(db).presence == "home"
        error = True
        assert tick(ha, db, config, at=AT + timedelta(minutes=3)) == (False, True)
        assert load_state(db).presence == "unknown"
        assert load_state(db).hold is None
        snapshot = json.loads(db.execute("SELECT snapshot_json FROM engine_state").fetchone()[0])
        assert snapshot["error"] == (
            "Home Assistant snapshot unavailable" if broken == "http" else "Home Assistant snapshot invalid"
        )
        error = False
        assert tick(ha, db, config, at=AT + timedelta(minutes=4)) == (True, True)
        assert load_state(db).hold.since == AT + timedelta(minutes=4)


def test_partial_publish_failure_does_not_lose_evaluation_and_next_tick_republishes(config, tmp_path):
    wifi, *_ = config["signals"]
    config["presence"]["home_hold_minutes"] = 0
    posted = []

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[reading(wifi, "ExampleWiFi")])
        posted.append(request.url.path)
        return httpx.Response(500 if len(posted) == 1 else 201)

    with client(handler) as ha, open_database(tmp_path / "situation.db") as db:
        assert tick(ha, db, config, at=AT) == (True, False)
        assert load_state(db).presence == "home"
        assert tick(ha, db, config, at=AT + timedelta(minutes=1)) == (True, True)
        assert posted == [f"/api/states/{PRESENCE_ENTITY}", f"/api/states/{ACTIVITY_ENTITY}"] * 2
        assert db.execute("SELECT COUNT(*) FROM presence_change").fetchone() == (1,)


def test_retention_during_failure_preserves_the_exact_boundary(config, tmp_path):
    def handler(request):
        return httpx.Response(503)

    with client(handler) as ha, open_database(tmp_path / "situation.db") as db:
        expired = (AT - timedelta(days=30, microseconds=1)).isoformat(timespec="microseconds")
        boundary = (AT - timedelta(days=30)).isoformat(timespec="microseconds")
        with db:
            db.executemany(
                "INSERT INTO signal_sample VALUES (?, 'example_old', 'none', NULL)", [(expired,), (boundary,)]
            )
            db.execute("INSERT INTO presence_change VALUES (?, 'away', 0.05, '[]')", (expired,))
        assert tick(ha, db, config, at=AT) == (False, False)
        assert db.execute("SELECT ts FROM signal_sample WHERE signal_id = 'example_old'").fetchall() == [(boundary,)]
        assert db.execute("SELECT presence FROM presence_change").fetchall() == [("unknown",)]


def test_same_minute_ticks_keep_first_sample_and_latest_evaluation(config, tmp_path):
    wifi, *_ = config["signals"]
    raw = "ExampleWiFi"
    config["presence"]["home_hold_minutes"] = 0

    def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json=[reading(wifi, raw)])
        return httpx.Response(201)

    with client(handler) as ha, open_database(tmp_path / "situation.db") as db:
        tick(ha, db, config, at=AT + timedelta(seconds=2))
        raw = "Not Connected"
        local = AT.astimezone(timezone(timedelta(hours=7)))
        tick(ha, db, config, at=local + timedelta(seconds=59, microseconds=999999))
        assert load_state(db).presence == "unknown"
        tick(ha, db, config, at=local + timedelta(minutes=1, seconds=4))
        assert db.execute("SELECT ts, says FROM signal_sample WHERE signal_id = ? ORDER BY ts", (wifi["id"],)).fetchall() == [
            (AT.isoformat(timespec="microseconds"), "home"),
            ((AT + timedelta(minutes=1)).isoformat(timespec="microseconds"), "away"),
        ]


def test_existing_database_and_sidecar_permissions_are_repaired(tmp_path):
    directory = tmp_path / "private"
    path = directory / "situation.db"
    files = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    with open_database(path):
        directory.chmod(0o755)
        for file in files:
            file.chmod(0o644)
        with open_database(path):
            assert stat.S_IMODE(directory.stat().st_mode) == 0o700
            assert all(stat.S_IMODE(file.stat().st_mode) == 0o600 for file in files)


def test_tick_cli_uses_configured_token_and_database(config, tmp_path, monkeypatch):
    database = tmp_path / "private" / "situation.db"
    config.update(database=str(database))
    config["home_assistant"].update(url="http://ha.example", token_env="EXAMPLE_HA_TOKEN")
    config_file = tmp_path / "example.yaml"
    config_file.write_text(yaml.safe_dump(config))
    monkeypatch.setenv("SITUATION_CONFIG", str(config_file))
    monkeypatch.setenv("EXAMPLE_HA_TOKEN", "example-cli-token")
    seen = []

    def handler(request):
        assert request.url.host == "ha.example"
        assert request.headers["Authorization"] == "Bearer example-cli-token"
        seen.append(request.url.path)
        return httpx.Response(200, json=[]) if request.method == "GET" else httpx.Response(201)

    factory = httpx.Client

    def mock_client(**kwargs):
        return factory(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("situation_hub.tick.httpx.Client", mock_client)
    assert main(["tick"]) == 0
    assert seen == ["/api/states", f"/api/states/{PRESENCE_ENTITY}", f"/api/states/{ACTIVITY_ENTITY}"]
    with closing(sqlite3.connect(database)) as db:
        assert db.execute("SELECT DISTINCT says, raw FROM signal_sample").fetchall() == [("none", None)]


@pytest.mark.parametrize("yaml_text", [
    "example: [example-private-value",
    "signals: [{id: example-private-value, kind: wifi, entity_id: sensor.example_phone}]",
    "home_assistant: {url: 'http://ha.example:example-private-value', token_env: EXAMPLE_TOKEN}\ndatabase: example.db",
])
def test_cli_config_errors_use_generic_messages(yaml_text, tmp_path, monkeypatch, capsys):
    path = tmp_path / "example.yaml"
    path.write_text(yaml_text)
    monkeypatch.setenv("SITUATION_CONFIG", str(path))
    assert main(["tick"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Situation configuration unavailable\n"


def test_launchagent_runs_one_tick_each_minute():
    path = ROOT / "ops/launchd/com.situation-hub.tick.plist.template"
    template = plistlib.loads(path.read_bytes())
    assert template["Label"] == "com.situation-hub.tick"
    assert template["ProgramArguments"][-1] == "tick"
    assert template["StartInterval"] == 60


def test_first_tick_preserves_historical_data_in_legacy_database(config, tmp_path):
    path = tmp_path / "situation.db"
    earlier = AT - timedelta(days=1)
    historical_sample = (earlier.isoformat(), "example_old", "home", "ExampleWiFi")
    historical_change = (earlier.isoformat(), "home", 0.96, '["example_old"]')
    with open_database(path) as db, db:
        db.execute("DROP TABLE engine_state")
        db.execute("INSERT INTO signal_sample VALUES (?, ?, ?, ?)", historical_sample)
        db.execute("INSERT INTO presence_change VALUES (?, ?, ?, ?)", historical_change)

    def handler(request):
        return httpx.Response(200, json=[]) if request.method == "GET" else httpx.Response(201)

    with client(handler) as ha, open_database(path) as db:
        assert tick(ha, db, config, at=AT) == (True, True)
        assert load_state(db).presence == "unknown"
        assert db.execute("SELECT * FROM signal_sample WHERE signal_id = 'example_old'").fetchone() == historical_sample
        assert db.execute("SELECT * FROM presence_change ORDER BY ts LIMIT 1").fetchone() == historical_change
