# Home Assistant state publication

The Python tick is the evaluator and publisher. On an upgrade, remove any
previously generated `packages/situation.yaml` and restart Home Assistant so its
template sensors stop writing the same entity IDs. Retire the old
`com.situation-hub.signal-log` LaunchAgent if installed. Existing SQLite signal
and transition history is retained. The first tick initializes the engine state.

## Configure and run

1. Copy `config.example.yaml` to `~/.config/situation-hub/config.yaml` and set
   mode `0600`. Set `SITUATION_CONFIG` if you use a different path. Fill in the
   sources and their failure domains in the private file.
2. Make a Home Assistant long-lived access token available to the tick process
   via the environment variable named by `home_assistant.token_env`.
3. Run a tick from the checkout, then check the entities in Home Assistant's
   **Developer tools → States**.

   ```sh
   uv run --locked situation-hub tick
   ```

For a macOS schedule, copy
`ops/launchd/com.situation-hub.tick.plist.template` to
`~/Library/LaunchAgents/com.situation-hub.tick.plist` and fill in the absolute
paths. Supply the token through the LaunchAgent's private runtime environment.
The program path can point to a private launcher that loads the token from the
host's credential store and runs the installed command with the remaining
arguments. Validate the plist with `plutil -lint` and load it with
`launchctl bootstrap`. The template runs one tick every 60 seconds.

## Verify the published states

Each tick requests `GET /api/states` once. After saving the evaluation to SQLite,
it publishes to `POST /api/states/sensor.situation_presence` and
`POST /api/states/sensor.situation_activity`. The next tick recreates the states
after a Home Assistant restart. [Home Assistant REST states API](https://developers.home-assistant.io/docs/api/rest/)

| Entity | State |
| --- | --- |
| `sensor.situation_presence` | `home`, `away`, or `unknown` |
| `sensor.situation_activity` | `asleep`, `home_active`, `home_idle`, `out`, or `unknown` |

Both have a `probability` attribute for P(home), `since` for that state's last
transition, `as_of` for the evaluation time, and the `evidence` and `against`
slug lists. Check `as_of` before using a state in an automation. Treat a missing,
future-dated, or older-than-three-minutes timestamp as unknown. The two REST writes
are separate, so consumers that need both states should require matching
`as_of` values. After a publish failure, the next tick retries both writes.
The CLI and agent use the local evaluation.

A phone reporting a known home Wi-Fi state can establish home after its hold.
For away, verify that a fresh GPS departure and a fresh observation from a
separate domain persist for the full hold. The
[contract](CONTRACT.md#2-evaluation-and-publication) describes the rules.

## Estimate signal weights

Label home and away intervals independently, then substitute their timestamps
in the query below. Run it with `sqlite3` against the configured database.
This estimates the likelihoods for bidirectional signals.

```sql
WITH labels(actual, start_utc, end_utc) AS (
    VALUES
        ('home', '2026-01-01T09:00:00Z', '2026-01-01T10:00:00Z'),
        ('away', '2026-01-01T11:00:00Z', '2026-01-01T12:00:00Z')
)
SELECT sample.signal_id,
       labels.actual,
       COUNT(*) AS total,
       SUM(sample.says <> 'none') AS fresh,
       ROUND(AVG(CASE WHEN sample.says <> 'none' THEN sample.says = 'home' END), 3)
           AS probability_of_home_vote
FROM signal_sample AS sample
JOIN labels ON julianday(sample.ts) >= julianday(labels.start_utc)
           AND julianday(sample.ts) < julianday(labels.end_utc)
GROUP BY sample.signal_id, labels.actual
ORDER BY sample.signal_id, labels.actual;
```

Estimate a home-only signal's likelihood from how often the positive state is
reported during independently verified periods when the source is fresh.
The query's average would consider only positive samples for that kind.
The tick reads weight changes from the private configuration on its next run.
