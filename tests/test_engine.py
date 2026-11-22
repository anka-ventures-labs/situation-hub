from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from situation_hub.config import load
from situation_hub.engine import evaluate
from situation_hub.votes import fresh_vote

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 11, 22, 12, tzinfo=UTC)


@pytest.fixture
def config():
    return load(ROOT / "config.example.yaml")


def reading(raw, reported):
    return {"state": raw, "last_reported": reported.isoformat()}


def run_timeline(config, minutes, report):
    previous = None
    for minute in range(minutes + 1):
        now = START + timedelta(minutes=minute)
        states = {}
        for signal in config["signals"]:
            sample = report(minute, signal["id"])
            if sample is not None:
                raw, reported_minute = sample
                states[signal["entity_id"]] = reading(raw, START + timedelta(minutes=reported_minute))
        result = evaluate(config, states, previous, at=now)
        previous = result.state
        yield result


def test_phone_on_home_wifi_sustains_home_through_hours_of_optional_updates(config):
    def report(minute, slug):
        return {
            "phone_wifi": ("ExampleWiFi", minute - minute % 4),
            "living_room_tv": ("off", minute - minute % 5),
            "phone_gps": ("home", minute - minute % 35),
            "phone_ping": ("on", minute - minute % 2) if minute >= 60 else None,
        }.get(slug)

    results = list(run_timeline(config, 240, report))
    assert [result.state.presence for result in results[:3]] == ["unknown", "unknown", "home"]
    assert {result.state.presence for result in results[2:]} == {"home"}
    assert {result.state.since for result in results[2:]} == {START + timedelta(minutes=2)}
    assert {result.state.activity for result in results[2:]} == {"home_idle"}
    assert results[21].snapshot["evidence"] == [{"signal": "phone_wifi", "says": "home", "age_min": 1}]


@pytest.mark.parametrize("gps_reports", [True, False])
def test_wifi_and_ping_loss_never_say_away_even_at_high_probability(config, gps_reports):
    # The two handset network votes produce old-model P(away) = 0.95.
    config["signals"][0].update(prob_given_home=0.85, prob_given_away=0.10)
    config["signals"][3].update(prob_given_home=0.82, prob_given_away=0.10)

    def report(minute, slug):
        at_home = minute < 5
        return {
            "phone_wifi": ("ExampleWiFi" if at_home else "Not Connected", minute),
            "phone_ping": ("on" if at_home else "off", minute),
            "phone_gps": ("home", minute if gps_reports else 0),
        }.get(slug)

    results = list(run_timeline(config, 60, report))
    assert results[2].state.presence == "home"
    assert {result.state.presence for result in results[5:]} == {"unknown"}
    assert results[-1].state.hold is None
    if not gps_reports:
        assert round(1 - results[-1].probability, 2) == 0.95


def test_departure_requires_fifteen_minutes_and_stays_away(config):
    def report(minute, slug):
        at_home = minute < 5
        elapsed = minute - 5 if not at_home else minute
        return {
            "phone_wifi": ("ExampleWiFi" if at_home else "Not Connected", minute - elapsed % 4),
            "phone_gps": ("home" if at_home else "not_home", minute - elapsed % 5),
            "phone_ping": ("on" if at_home else "off", minute - elapsed % 2),
            "living_room_tv": ("off", minute),
            "asleep": ("off", minute),
        }.get(slug)

    results = list(run_timeline(config, 180, report))
    assert {result.state.presence for result in results[5:20]} == {"unknown"}
    assert {result.state.presence for result in results[20:]} == {"away"}
    assert {result.state.activity for result in results[20:]} == {"out"}
    assert results[20].state.since == START + timedelta(minutes=20)
    assert results[20].snapshot["confidence"] == pytest.approx(1 - results[20].probability)
    assert {entry["signal"] for entry in results[-1].snapshot["evidence"]} == {
        "phone_wifi", "phone_gps", "phone_ping",
    }


def test_overnight_network_loss_and_sleep_never_say_away(config):
    def report(minute, slug):
        return {
            "phone_wifi": ("Not Connected", 0),
            "phone_gps": ("not_home", -30),
            "phone_ping": ("off", minute if minute < 120 else 119),
            "asleep": ("on", minute - minute % 10),
        }.get(slug)

    results = list(run_timeline(config, 8 * 60, report))
    assert {result.state.presence for result in results} == {"unknown", "home"}
    assert results[120].state.presence == "unknown"
    assert results[8 * 60].state.activity == "asleep"


@pytest.mark.parametrize("gps_is_fresh", [True, False])
def test_away_needs_both_location_and_an_independent_domain(config, gps_is_fresh):
    if gps_is_fresh:
        config["signals"][2]["domain"] = "phone_network"
    else:
        config["signals"][3]["domain"] = "separate_network"

    def report(minute, slug):
        return {
            "phone_wifi": ("Not Connected", minute),
            "phone_gps": ("not_home", minute if gps_is_fresh else -30),
            "phone_ping": ("off", minute),
        }.get(slug)

    results = list(run_timeline(config, 25, report))
    assert results[-1].probability < config["presence"]["away_threshold"]
    assert {result.state.presence for result in results} == {"unknown"}
    assert all(result.state.hold is None for result in results)


def test_expired_strong_witness_cannot_lend_old_probability_to_fresh_votes(config):
    # Wi-Fi and GPS have likelihood ratios 1/2 and 4/7. Together they give P(home) = 0.30.
    # The third witness used to pull the value down to 0.01 after rounding.
    config["signals"][0].update(prob_given_home=0.60, prob_given_away=0.20)
    config["signals"][2].update(prob_given_home=0.60, prob_given_away=0.30)
    config["signals"][3].update(prob_given_home=0.98, prob_given_away=0.10)

    def report(minute, slug):
        return {
            "phone_wifi": ("Not Connected", minute),
            "phone_gps": ("not_home", minute),
            "phone_ping": ("off", -10),
        }.get(slug)

    results = list(run_timeline(config, 35, report))
    assert round(results[0].probability, 2) == 0.01
    assert results[10].state.hold is not None
    assert results[11].probability == pytest.approx(0.30)
    assert results[11].state.hold is None
    assert all(result.state.presence == "unknown" for result in results)


@pytest.mark.parametrize("renewal", [10.75, 11, 12])
def test_away_hold_restarts_for_expiry_between_ticks_or_at_a_tick(config, renewal):
    # The previous GPS report expires at 10:30. A new report at 10:45 means
    # both minute samples can see an away vote but the hold cannot span the gap.
    config["signals"][2]["stale_after_minutes"] = 10.5

    def report(minute, slug):
        gps_report = renewal if minute >= renewal else 0
        if minute > renewal + 1:
            gps_report = minute
        return {
            "phone_wifi": ("Not Connected", minute),
            "phone_gps": ("not_home", gps_report),
        }.get(slug)

    results = list(run_timeline(config, 29, report))
    restarted = 11 if renewal <= 11 else 12
    assert results[10].state.hold.since == START
    assert results[restarted].state.hold.since == START + timedelta(minutes=restarted)
    assert {result.state.presence for result in results[:restarted + 15]} == {"unknown"}
    assert results[restarted + 15].state.presence == "away"


def test_new_qualifying_away_witness_restarts_hold(config):
    def report(minute, slug):
        return {
            "phone_wifi": ("Not Connected", minute),
            "phone_gps": ("not_home", minute),
            "phone_ping": ("off", minute) if minute >= 12 else None,
        }.get(slug)

    results = list(run_timeline(config, 28, report))
    assert results[12].state.hold.since == START + timedelta(minutes=12)
    assert results[26].state.presence == "unknown"
    assert results[27].state.presence == "away"


def test_missed_ticks_reset_hold_even_with_fresh_evidence(config):
    config["presence"]["away_hold_minutes"] = 5
    states = {
        config["signals"][0]["entity_id"]: reading("Not Connected", START),
        config["signals"][2]["entity_id"]: reading("not_home", START),
    }
    first = evaluate(config, states, None, at=START)
    next_tick = START + timedelta(minutes=5)
    result = evaluate(config, states, first.state, at=next_tick)
    assert result.state.presence == "unknown"
    assert result.state.hold.since == next_tick


def test_away_excludes_fresh_home_vote_even_when_posterior_is_low(config):
    def report(minute, slug):
        return {
            "phone_wifi": ("Not Connected", minute),
            "phone_gps": ("not_home", minute),
            "phone_ping": ("off", minute),
            "asleep": ("on", minute),
        }.get(slug)

    results = list(run_timeline(config, 20, report))
    assert results[-1].probability < 0.2
    assert {result.state.presence for result in results} == {"unknown"}


@pytest.mark.parametrize(("kind", "raw", "expected"), [
    ("wifi", "ExampleWiFi", "home"), ("wifi", "Not Connected", "away"),
    ("gps_zone", "home", "home"), ("gps_zone", "not_home", "away"),
    ("media_playing", "playing", "home"), ("media_playing", "paused", None),
    ("media_playing", "off", None), ("asleep", "on", "home"), ("asleep", "off", None),
    ("device_active", "on", "home"), ("device_active", "off", None),
    ("ping", "on", "home"), ("ping", "off", "away"), ("ping", "other", None),
    ("wifi", "unavailable", None), ("wifi", "unknown", None), ("wifi", "", None),
    ("wifi", "\ud800", None),
])
def test_vote_mapping(kind, raw, expected):
    signal = {"kind": kind, "home_values": ["ExampleWiFi", "home"]}
    vote = fresh_vote(signal, reading(raw, START), START)
    assert (vote.says if vote else None) == expected


def test_datetime_freshness_at_fractional_boundary_and_timestamp_precedence():
    signal = {"kind": "ping", "stale_after_minutes": 0.125}
    at = START + timedelta(microseconds=123456)
    boundary = at - timedelta(seconds=7, microseconds=500000)
    state = reading("on", boundary)
    state["last_updated"] = at.isoformat()
    assert fresh_vote(signal, state, at).age_minutes == 0
    assert fresh_vote(signal, state, at + timedelta(microseconds=1)) is None
    state["last_reported"] = "unparseable"
    assert fresh_vote(signal, state, at).says == "home"
    state["last_reported"] = (at + timedelta(microseconds=1)).isoformat()
    assert fresh_vote(signal, state, at) is None
    del state["last_updated"]
    state["last_reported"] = "invalid"
    state["last_changed"] = at.isoformat()
    assert fresh_vote(signal, state, at).says == "home"


def test_published_away_becomes_unknown_when_a_witness_expires(config):
    config["signals"][2]["stale_after_minutes"] = 18

    def report(minute, slug):
        return {
            "phone_wifi": ("Not Connected", minute),
            "phone_gps": ("not_home", minute if minute >= 21 else 0),
        }.get(slug)

    results = list(run_timeline(config, 37, report))
    assert {result.state.presence for result in results[15:19]} == {"away"}
    assert results[19].state.hold is None
    assert {result.state.presence for result in results[19:36]} == {"unknown"}
    assert results[36].state.presence == "away"
    assert results[36].state.since == START + timedelta(minutes=36)


def test_legacy_activity_sleep_source_is_freshness_gated(config):
    config["presence"]["home_hold_minutes"] = 0
    config["activity"] = {"asleep_entity": "binary_sensor.example_activity_sleep"}
    wifi, tv, *_ = config["signals"]
    at = START + timedelta(minutes=21)
    states = {
        wifi["entity_id"]: reading("ExampleWiFi", at),
        tv["entity_id"]: reading("playing", at),
        config["activity"]["asleep_entity"]: reading("on", START),
    }
    result = evaluate(config, states, None, at=at)
    assert result.state.activity == "home_active"
    states[config["activity"]["asleep_entity"]] = reading("on", at)
    result = evaluate(config, states, result.state, at=at)
    assert result.state.activity == "asleep"
    assert "example_activity_sleep" not in str(result.snapshot)
