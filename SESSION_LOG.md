# Session log

## 2026-11-22: contract

- An existing home automation acted on a single presence signal. The project defines
  how to combine observations and use uncertainty in later action decisions.
- The repository is public. Initial action scope covers reversible device changes.
- Wrote `docs/CONTRACT.md` (config shape, HA entity names, signal log, `situation()` output,
  action log, risk tiers), `docs/BACKLOG.md`, README, `config.example.yaml`.
- Added the shared package skeleton (pyproject, cli registry, config loader, smoke test) so workstreams extend it without editing each other's files.
- Next: dispatch A (HA package generator + signal logger) and B (`situation()` MCP server).

## 2026-11-22 · HA package and signal logger

- Added `ha-package` to generate native Bayesian and template entities from the private config.
  One availability-gated helper per signal lets Home Assistant skip stale evidence. State
  triggers apply the presence hold times, and the package uses HA's published probability.
- Added `log-signals` for minute sampling and `--once` for launchd. The two SQLite tables use
  UTC timestamps, 30-day retention, WAL, and private file permissions.
- Verified the example package and mocked REST and SQLite flows with the locked pytest suite.
  Installation and live verification remained pending.

## 2026-11-22 · Workstream B

- Added the read-only `situation()` stdio MCP tool and `situation-hub situation` CLI using
  the same snapshot reader. The locked MCP SDK supplies the transport and tool annotations.
  The existing HTTP client uses Home Assistant's authenticated per-entity state endpoint.
- Signal ages use `last_reported` with a `last_updated` fallback. Signals without a fresh,
  usable reading go in `stale`. Calendar is neutral because the contract gives it no vote
  mapping. Missing entities and failed reads produce an unknown presence and a generic
  error. The result includes signal slugs and derived values.
- Added offline pytest coverage with mocked HA reads and in-process MCP clients, including
  the legacy MCP protocol. `uv run --locked pytest -q` passed all 19 tests. An offline
  smoke test checked that the installed stdio and CLI commands return the same snapshot.
  Added [MCP setup and response examples](docs/AGENT.md).
- Live verification waits on package installation and MCP client registration.

## 2026-11-22 · R2 presence and privacy fixes

- The publisher and reader now require two fresh witnesses in the same direction,
  reject contradictory evidence, and apply a one-point margin to rounded HA
  probability. The reader fetches one REST snapshot and clears confidence when
  its evidence cannot support the published direction.
- Shared vote rules cover neutral activity inputs, timestamp precedence, and a
  20-minute default. Duplicate source entities and unsupported kinds fail
  configuration validation. Hold completion rechecks current votes.
- Package generation requires a private output file. Logging keeps partial reads,
  deduplicates samples per UTC minute, and repairs database directory and sidecar
  permissions at open.
- Locked pytest passed 65 tests offline. With locally available Jinja2, all 68 tests
  passed, including the generated-template regressions. Live package validation remains pending.

## 2026-11-22: coordinator assembly

- Merged the review fix round (941cf0a). Added jinja2 as a dev dependency so the generated-template
  regression tests run by default. `uv run --locked pytest -q` → 68 passed.

## 2026-11-22 · Single presence engine

- Replaced the generated HA package and per-entity logger with `situation-hub tick`.
  Each tick evaluates one source snapshot, commits the private samples, transitions,
  and current hold in SQLite, then publishes presence and activity to HA.
- Probability uses fresh votes only. Away requires an affirmative GPS departure,
  a separate corroborating domain, and zero home votes. A strong home witness can sustain
  a hold when optional inputs expire. Recorded report times break a hold after
  a freshness gap, including gaps between ticks.
- The CLI and MCP tool read the same local evaluation and return unknown when
  the engine is stale. Configuration diagnostics omit private values and a
  malformed source state leaves the other samples available.
- The locked offline suite passed 65 tests on Python 3.14. The source and tests
  compiled on Python 3.12. An offline 3.12 suite run requires a cached PyYAML
  wheel for that interpreter. The new LaunchAgent template passed `plutil -lint`.
- Live work remains private configuration migration and HA verification of the
  scheduled tick and its two published entities.

## 2026-11-22: live check and first release

- Two independent reviews (no-ship twice) led to the single Python engine design. The engine ran
  against a live Home Assistant: with a strong uncontradicted Wi-Fi signal, GPS and ping all
  saying home, it settled on `home` within the 2-minute hold and published the state to HA.
- Lesson from the live run: many phone integrations write a new timestamp only when the state
  changes, so change-driven signals (Wi-Fi, GPS zone, ping) need long `stale_after_minutes`
  (hours, not minutes). This only biases towards `home`/`unknown`; `away` still needs a GPS
  departure plus an independent domain. Documented in config.example.yaml comments.
- Added GitHub Actions (pytest on 3.12 and 3.14, locked uv). History squashed into one commit so
  no intermediate commit carries host-specific wording.
