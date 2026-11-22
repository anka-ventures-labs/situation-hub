# Backlog

Remaining work only. History goes in `SESSION_LOG.md`. A row closes only when it is live for
the owner (with evidence) or explicitly declined (with the reason).

## Owner requests

| # | Date | Request (condensed) | State | Waits on |
| --- | --- | --- | --- | --- |
| R1 | 2026-11-22 | Combine independent observations and withhold actions when presence is uncertain | Pending live integration | A, B, C, D, E, F, G |
| R2 | 2026-11-22 | Public repo with personal information kept out | Done: this repo; contract privacy rule | — |
| R3 | 2026-11-22 | No high-risk devices | Initial scope: reversible device changes | — |

## Work rows

| ID | Item | State | Waits on |
| --- | --- | --- | --- |
| S0 | Contract (`docs/CONTRACT.md`) | Written | — |
| A | Python evaluator, minute tick, SQLite history, and HA publication | Home and away timelines, hold continuity, and REST publication verified offline | Installation, private domain and weight review, and live HA validation |
| B | `situation()` MCP server | Local evaluation and stale-engine handling verified offline | A installed, live MCP read |
| C | Health source publishes sleep and recovery signals to HA | Planned | Health provider integration |
| D | Guardrails engine: risk tiers, action log, undo, notify | Planned | S0, B |
| E | Event triggers (wake, arrive, leave, download finished) | Planned | A, C |
| F | Media and host-health read tools | Planned | — |
| G | HA cleanup: expose only the configured device set to the agent | Planned | D |
