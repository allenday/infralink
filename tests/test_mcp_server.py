"""MCP is the native projection of the public Infralink operation registry."""

from __future__ import annotations

import asyncio
import tomllib
from pathlib import Path

from mcp import Client

from infralink.mcp_server import create_server
from infralink.operator_surface import operator_surface

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_mcp_transport_has_a_dedicated_entrypoint_and_the_public_server_identity() -> None:
    """The transport launcher cannot introduce a second command registry."""
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert project["project"]["scripts"]["infralink-mcp"] == "infralink.mcp_server:run"
    assert create_server().name == "infralink"


def test_mcp_has_no_generic_argv_bridge_and_lists_all_registered_operations() -> None:
    async def exercise() -> set[str]:
        async with Client(create_server()) as client:
            page = await client.list_tools()
            names = {tool.name for tool in page.tools}
            while page.next_cursor is not None:
                page = await client.list_tools(cursor=page.next_cursor)
                names.update(tool.name for tool in page.tools)
        return names

    names = asyncio.run(exercise())
    assert "infralink_command" not in names
    assert names == {operation.name for operation in operator_surface.operations.list()}


def test_mcp_returns_the_canonical_typed_version_envelope() -> None:
    async def exercise() -> dict[str, object]:
        async with Client(create_server()) as client:
            result = await client.call_tool("version", {})
        assert result.is_error is False
        return result.structured_content

    payload = asyncio.run(exercise())
    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["command"]["parsed"]["path"] == ["version"]
