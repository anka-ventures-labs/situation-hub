"""Evaluate presence and activity from one coherent Home Assistant snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
from typing import Any

from situation_hub.config import presence_settings
from situation_hub.votes import Vote, fresh_vote, raw_state, utc_iso, within_freshness

MAX_TICK_AGE = timedelta(minutes=3)
PRESENCE_ENTITY = "sensor.situation_presence"
ACTIVITY_ENTITY = "sensor.situation_activity"
ACTIVE_KINDS = {"media_playing", "device_active"}


@dataclass(frozen=True)
class Hold:
    direction: str
    since: datetime
    reports: dict[str, datetime]


@dataclass(frozen=True)
class EngineState:
    at: datetime
    presence: str
    since: datetime
    activity: str
    activity_since: datetime
    hold: Hold | None


@dataclass(frozen=True)
class Evaluation:
    state: EngineState
    probability: float
    snapshot: dict[str, Any]
    samples: list[tuple[str, str, str | None]]


def _log_likelihood(signal: dict[str, Any], says: str, actual: str) -> float:
    probability = signal[f"prob_given_{actual}"]
    return math.log(probability) if says == "home" else math.log1p(-probability)


def _probability(
    prior: float, signals: dict[str, dict[str, Any]], votes: dict[str, Vote]
) -> float:
    if not votes:
        return prior
    home = math.log(prior) + math.fsum(
        _log_likelihood(signals[slug], vote.says, "home") for slug, vote in votes.items()
    )
    away = math.log1p(-prior) + math.fsum(
        _log_likelihood(signals[slug], vote.says, "away") for slug, vote in votes.items()
    )
    # Scale by the larger score before exponentiating to avoid underflow.
    scale = max(home, away)
    home, away = math.exp(home - scale), math.exp(away - scale)
    return home / (home + away)


def _qualifying_votes(
    settings: dict[str, float],
    signals: dict[str, dict[str, Any]],
    votes: dict[str, Vote],
    probability: float,
    previous: Hold | None,
) -> tuple[str, dict[str, Vote]]:
    home = {slug: vote for slug, vote in votes.items() if vote.says == "home"}
    away = {slug: vote for slug, vote in votes.items() if vote.says == "away"}
    if home and not away and probability >= settings["home_threshold"]:
        # Keep the current support when it still meets the threshold by itself.
        if (
            previous is not None
            and previous.direction == "home"
            and previous.reports.keys() <= home.keys()
        ):
            retained = {slug: home[slug] for slug in previous.reports}
            if _probability(settings["prior_home"], signals, retained) >= settings["home_threshold"]:
                return "home", retained
        for slug, vote in home.items():
            if _probability(settings["prior_home"], signals, {slug: vote}) >= settings["home_threshold"]:
                return "home", {slug: vote}
        return "home", home
    if (
        not home
        and probability <= settings["away_threshold"]
        and len({signals[slug]["domain"] for slug in away}) >= 2
        and any(signals[slug]["kind"] == "gps_zone" for slug in away)
    ):
        return "away", away
    return "unknown", {}


def _next_hold(
    direction: str,
    qualifying: dict[str, Vote],
    signals: dict[str, dict[str, Any]],
    at: datetime,
    previous: Hold | None,
) -> Hold | None:
    if not qualifying:
        return None
    reports = {slug: vote.reported for slug, vote in qualifying.items()}
    since = at
    if (
        previous is not None
        and direction == previous.direction
        and reports.keys() == previous.reports.keys()
        and all(
            within_freshness(signals[slug], previous.reports[slug], reported)
            for slug, reported in reports.items()
        )
    ):
        # A renewal after the previous report expired leaves a gap even when both
        # snapshots had fresh evidence. A backwards report also breaks continuity.
        since = previous.since
    return Hold(direction, since, reports)


def _activity(
    presence: str,
    config: dict[str, Any],
    signals: dict[str, dict[str, Any]],
    votes: dict[str, Vote],
    states: dict[str, dict[str, Any]],
    at: datetime,
) -> str:
    if presence == "away":
        return "out"
    if presence != "home":
        return "unknown"

    kinds = {signals[slug]["kind"] for slug, vote in votes.items() if vote.says == "home"}
    if "asleep" in kinds:
        return "asleep"
    asleep = (config.get("activity") or {}).get("asleep_entity")
    if asleep:
        source = next((signal for signal in signals.values() if signal["entity_id"] == asleep), {})
        if fresh_vote({**source, "kind": "asleep"}, states.get(asleep), at):
            return "asleep"
    return "home_active" if kinds & ACTIVE_KINDS else "home_idle"


def evaluate(
    config: dict[str, Any],
    states: dict[str, dict[str, Any]],
    previous: EngineState | None,
    *,
    at: datetime,
    error: str | None = None,
) -> Evaluation:
    at = at.astimezone(UTC)
    settings = presence_settings(config)
    signals = {signal["id"]: signal for signal in config.get("signals", [])}
    votes = {}
    samples = []
    evidence_by_direction = {"home": [], "away": []}
    stale = []
    for slug, signal in signals.items():
        reading = states.get(signal["entity_id"])
        vote = fresh_vote(signal, reading, at)
        samples.append((slug, vote.says if vote else "none", raw_state(reading)))
        if vote is None:
            stale.append(slug)
            continue
        votes[slug] = vote
        evidence_by_direction[vote.says].append(
            {"signal": slug, "says": vote.says, "age_min": vote.age_minutes}
        )

    probability = _probability(settings["prior_home"], signals, votes)
    previous_hold = (
        previous.hold if previous is not None and timedelta(0) <= at - previous.at <= MAX_TICK_AGE
        else None
    )
    direction, qualifying = _qualifying_votes(settings, signals, votes, probability, previous_hold)
    hold = _next_hold(direction, qualifying, signals, at, previous_hold)
    presence = "unknown"
    if hold is not None:
        held_seconds = (at - hold.since).total_seconds()
        if held_seconds >= settings[f"{direction}_hold_minutes"] * 60:
            presence = direction

    activity = _activity(presence, config, signals, votes, states, at)
    since = previous.since if previous is not None and previous.presence == presence else at
    activity_since = (
        previous.activity_since if previous is not None and previous.activity == activity else at
    )
    evidence, against = (
        (evidence_by_direction["away"], evidence_by_direction["home"]) if presence == "away"
        else (evidence_by_direction["home"], evidence_by_direction["away"])
    )
    confidence = None
    if presence == "home":
        confidence = probability
    elif presence == "away":
        confidence = 1 - probability
    snapshot = {
        "presence": presence,
        "confidence": confidence,
        "since": utc_iso(since),
        "activity": activity,
        "evidence": evidence,
        "against": against,
        "stale": stale,
        "as_of": utc_iso(at),
    }
    if error is not None:
        snapshot["error"] = error
    state = EngineState(at, presence, since, activity, activity_since, hold)
    return Evaluation(state, probability, snapshot, samples)
