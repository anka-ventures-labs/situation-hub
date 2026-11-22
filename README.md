# situation-hub

A small home service that answers one question for a personal assistant agent:
**where is the owner, what are they doing, and how sure are we?** The Python
engine weighs observations such as phone Wi-Fi, GPS, media playback, and sleep.
It reports `home`, `away`, or `unknown`, publishes to Home Assistant, and
serves the stored result through a read-only `situation()` MCP tool.

The evaluator and reader are verified offline. Device action guardrails and
live integration are tracked in the [backlog](docs/BACKLOG.md). See the
[contract](docs/CONTRACT.md) and [Home Assistant setup](docs/HA_PACKAGE.md).

## Privacy

This repository is public and holds no personal data. Entity IDs, network names, chat IDs,
tuned thresholds and logs live only in the private config (`config.example.yaml` shows the
shape) and the private database on the host.

## Licence

MIT
