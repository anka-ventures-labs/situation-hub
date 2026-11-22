# Use situation-hub with an MCP client

`situation()` takes no arguments and returns the stored evaluation described in
the [situation contract](CONTRACT.md#4-situation-cli-and-mcp-tool). The official
MCP Python SDK provides stdio transport and the read-only tool annotation. The
server reads the SQLite database on the same host using the private config.
The scheduled tick owns Home Assistant access.

## Register the local server in Hermes

Run `uv sync --locked` in the checkout that Hermes can access. Add this entry
to the active Hermes profile's `config.yaml`, using absolute paths for the
executable and installed private configuration. Run the server as a user with
read access to the private configuration and database.
[Hermes MCP config reference](https://hermes-agent.nousresearch.com/docs/reference/mcp-config-reference)

```yaml
mcp_servers:
  situation_hub:
    command: "/path/to/situation-hub/.venv/bin/situation-hub-mcp"
    env:
      SITUATION_CONFIG: "/path/to/private/config.yaml"
```

Add `situation_hub` to the existing `platform_toolsets.<platform>` list for
each platform that should use it and preserve its other entries. Run
`hermes mcp test situation_hub` to check discovery. Use
`uv run --locked situation-hub situation` with the same configuration to read
the JSON from the shell.
[Hermes toolsets reference](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference)

## Read the result

Home and away return confidence in the reported direction. An unknown
evaluation has null confidence. Its home votes appear in `evidence` and its
away votes in `against`. During an outage, `error` gives a generic diagnostic.
If the engine has stopped ticking, the reader returns the following shape.

```json
{
  "presence": "unknown",
  "confidence": null,
  "since": null,
  "activity": "unknown",
  "evidence": [],
  "against": [],
  "stale": ["phone_wifi", "living_room_tv", "phone_gps", "phone_ping", "asleep"],
  "as_of": "2026-11-22T12:13:05Z",
  "error": "engine stale"
}
```

Use generic signal IDs because the MCP client may send them to a model
provider. Every evidence age is measured at `as_of` in whole minutes.
