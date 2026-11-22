"""Stdio MCP server for the read-only situation tool."""

from mcp.server import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations

from situation_hub.situation import encode_snapshot, read_situation

server = MCPServer("situation-hub", log_level="WARNING")


@server.tool(
    annotations=ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    )
)
def situation() -> CallToolResult:
    """Read the owner's current presence, activity, confidence, and supporting signals."""
    snapshot = read_situation()
    return CallToolResult(
        content=[TextContent(type="text", text=encode_snapshot(snapshot))],
        structured_content=snapshot,
    )


def main() -> None:
    server.run(transport="stdio")
