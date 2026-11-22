import asyncio
import json

import pytest
from mcp import Client
from mcp.types import TextContent

from situation_hub import cli
from situation_hub.mcp_server import server


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_sdk_lists_and_calls_read_only_tool(mode, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("SITUATION_CONFIG", str(tmp_path / "missing.yaml"))

    async def list_and_call():
        async with Client(server, mode=mode) as client:
            tools = (await client.list_tools()).tools
            assert [tool.name for tool in tools] == ["situation"]
            tool = tools[0]
            assert tool.input_schema["type"] == "object"
            assert tool.input_schema["properties"] == {}
            assert not tool.input_schema.get("required")
            assert tool.annotations is not None
            assert tool.annotations.read_only_hint is True
            assert tool.annotations.destructive_hint is False
            assert tool.annotations.idempotent_hint is True
            assert tool.annotations.open_world_hint is False
            return await client.call_tool("situation", {})

    result = asyncio.run(list_and_call())
    assert not result.is_error
    content, = result.content
    assert isinstance(content, TextContent)
    payload = json.loads(content.text)
    assert result.structured_content == payload
    assert payload["presence"] == "unknown"
    assert payload["confidence"] is None
    assert payload["error"] == "Situation configuration unavailable"
    assert str(tmp_path) not in content.text

    assert cli.main(["situation"]) == 0
    cli_payload = json.loads(capsys.readouterr().out)
    assert cli_payload.pop("as_of").endswith("Z")
    assert payload.pop("as_of").endswith("Z")
    assert cli_payload == payload
