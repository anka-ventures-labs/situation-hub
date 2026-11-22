# Situation contract

The Python engine evaluates presence and activity. Home Assistant displays the
published state. The CLI and MCP tool read the stored evaluation.

This repository is public. Keep real entity IDs, network names, chat IDs, home
coordinates, tuned weights, and logs in the private configuration or database on
the host. Use generic signal IDs because evidence can be sent to a model provider.

## 1. Private configuration

Set `SITUATION_CONFIG` to the private configuration file or use the default
`~/.config/situation-hub/config.yaml`. Give the file mode `0600`. The
[example configuration](../config.example.yaml) shows the full structure.

- Each signal needs a unique `id`, a distinct Home Assistant `entity_id`, a
  `kind`, a failure `domain`, and the two likelihoods below. IDs and domains
  are slugs. Signals that can fail together share a domain. For example, Wi-Fi
  and ping for the same phone belong to `phone_network`, and GPS belongs to
  `phone_location`.
- `prob_given_home` and `prob_given_away` give the likelihood of a home vote
  under each actual presence state. Set both strictly between zero and one.
  Wi-Fi and GPS also need a list of `home_values`.
- The presence defaults are `prior_home: 0.6`, `home_threshold: 0.8`,
  `away_threshold: 0.2`, `home_hold_minutes: 2`, and
  `away_hold_minutes: 15`. The prior must be strictly between zero and one.
  Thresholds must satisfy `0 <= away_threshold < home_threshold <= 1`. Hold
  lengths and staleness limits are nonnegative and accept fractional minutes.
- An `asleep` signal contributes to both presence and activity. An optional
  `activity.asleep_entity` can supply sleep for activity alone. It uses the
  configured freshness of a matching signal or the 20-minute default.

| Kind | Home vote | Away vote |
| --- | --- | --- |
| `wifi`, `gps_zone` | State matches a configured home value | Any other usable state |
| `ping` | `on` | `off` |
| `media_playing` | `playing` | Neutral |
| `asleep`, `device_active` | `on` | Neutral |

States outside the listed cases are neutral. Missing, empty, `unknown`,
`unavailable`, and malformed Unicode readings are neutral for every kind.
The engine uses the first valid timezone-aware timestamp among `last_reported`,
`last_updated`, and `last_changed`. It subtracts that timestamp from the
evaluation time. A vote is fresh from zero through `stale_after_minutes`
inclusive. The default is 20 minutes. A missing or future timestamp gives no vote.

## 2. Evaluation and publication

Each tick fetches one authenticated `GET /api/states` snapshot. For each
hypothesis (home or away), the engine multiplies its prior by each fresh vote's
likelihood. A home vote contributes the configured likelihood, an away vote
contributes its complement, and a neutral reading contributes no factor. The
engine normalizes the two scores to obtain P(home) and compares it to the
configured thresholds at full precision.

- **Home** requires at least one fresh home vote, zero fresh away votes, and
  P(home) at or above `home_threshold` for `home_hold_minutes`. A single
  strong signal can reach home even when idle media and quiet GPS provide no
  evidence. The engine retains a still-sufficient home support set when more
  optional home votes appear. When selecting support, it tries individual
  voters in configuration order before using all fresh home votes. All fresh
  votes still contribute to the current probability.
- **Away** requires P(home) at or below `away_threshold` for
  `away_hold_minutes`, fresh away votes from at least two failure domains, at
  least one fresh `gps_zone` away vote, and zero fresh home votes. Network
  signals from one handset can drop together, including while the owner is
  home. A reported departure and corroboration from another failure domain
  are required even when the network votes produce a low P(home).
- **Unknown** applies while a hold is pending, when evidence conflicts, or
  when either direction lacks sufficient fresh evidence.

A hold starts at the first qualifying tick. It restarts when its direction or
qualifying signal set changes, or when a supporting witness stops qualifying.
Away uses all fresh away votes as its qualifying set. Home uses the support set
described above. The engine compares each supporting witness's report time to
its previously observed report. A renewal after the earlier report's expiry
breaks the hold even if it arrives between ticks. A backwards report or more
than three minutes between ticks also breaks continuity. The engine checks the
hold at every tick, including after a directional state has been published.

Activity is `out` for away and `unknown` for unknown presence. At home, fresh
sleep evidence yields `asleep`, fresh playing media or device activity yields
`home_active`, and otherwise activity is `home_idle`.

Home Assistant receives `sensor.situation_presence` and
`sensor.situation_activity`, with P(home), per-entity `since`, evaluation
`as_of`, and evidence slugs in their attributes. See
[Home Assistant setup](HA_PACKAGE.md) for endpoints and freshness.

## 3. Database

The tick commits samples, presence transitions, and the latest evaluation and
hold in one SQLite transaction before publishing to Home Assistant. An invalid
or failed source snapshot clears a hold and records an unknown evaluation. A
failed publish leaves the evaluation available to local readers.

The database uses WAL. The database and its sidecars use mode `0600`. The
containing directory uses mode `0700`. Use a dedicated directory for the database.

```sql
CREATE TABLE signal_sample (
  ts TEXT NOT NULL,          -- UTC ISO 8601 truncated to the minute
  signal_id TEXT NOT NULL,   -- config slug
  says TEXT NOT NULL,        -- home | away | none
  raw TEXT,                  -- raw HA state, kept on the host
  PRIMARY KEY (ts, signal_id));
CREATE TABLE presence_change (
  ts TEXT PRIMARY KEY, presence TEXT NOT NULL, probability REAL NOT NULL, evidence_json TEXT NOT NULL);
```

The first sample for each signal in a UTC minute is retained. A missing or
unusable reading casts no vote. The sample and transition logs retain 30 days.
The singleton `engine_state` row holds the latest public snapshot, probability,
activity transition time, and the current hold's supporting slugs and report times.

## 4. Situation CLI and MCP tool

`situation-hub situation` and the read-only `situation()` tool return the same
JSON from the local database.

```json
{
  "presence": "home",
  "confidence": 0.94,
  "since": "2026-11-22T11:40:00Z",
  "activity": "home_idle",
  "evidence": [{"signal": "phone_wifi", "says": "home", "age_min": 1}],
  "against": [],
  "stale": ["living_room_tv", "phone_gps", "phone_ping", "asleep"],
  "as_of": "2026-11-22T12:13:05Z"
}
```

`confidence` is P(home) for home, 1 − P(home) for away, and null for unknown.
It is a model estimate. `since` is when the published presence last changed.
`as_of` is the evaluation time. `evidence` contains fresh votes in the reported
direction and `against` lists votes in the opposite direction. For unknown,
home votes go in `evidence` and away votes in `against`. Ages are whole minutes
at evaluation. `stale` lists every signal that cast no vote, including neutral
readings.

If no tick has run, or the latest tick is over three minutes old or future-dated,
the reader returns unknown with `error: "engine stale"`, null confidence and
`since`, and empty vote lists. When available, `as_of` still identifies the last
evaluation. Configuration and database failures produce generic diagnostics.
The response contains only signal slugs and derived values.

## 5. Action log (planned)

```sql
CREATE TABLE action_log (
  id INTEGER PRIMARY KEY,
  ts TEXT NOT NULL,
  action TEXT NOT NULL,          -- e.g. turn_off
  target TEXT NOT NULL,          -- device label from config
  tier INTEGER NOT NULL,
  decision TEXT NOT NULL,        -- done | asked | refused | delayed
  situation_json TEXT NOT NULL,  -- the situation() snapshot used
  prior_state_json TEXT,         -- for undo
  result TEXT,
  undone_at TEXT);
```

## 6. Risk tiers (planned)

| Tier | Meaning | Rule |
| --- | --- | --- |
| 0 | Inform | Always allowed |
| 1 | Harmless (queue a download, adjust a scene) | Allowed |
| 2 | Reversible device change (lights, AC, TV off) | Only when presence is `away` or activity `asleep` with confidence ≥ 0.9. Log and notify with undo (1 hour). |
| 3 | Disruptive | Confidence ≥ 0.97, no contrary evidence in 15 min, announce and wait 10 min |
| 4 | Never alone | Always ask |

`unknown` presence never permits tier 2 or above. Devices missing from the config
are tier 4. An undo within 10 minutes is recorded as a mistake for the weekly review.
