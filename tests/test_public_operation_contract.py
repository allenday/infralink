"""Contract coverage for every public Infralink operation projection.

The public executable is the generated Click projection of ``operator_surface``
and MCP is its sibling projection.  Historical hand-written command spellings
are intentionally covered only as retired negative contracts below.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
import yaml
from click.testing import CliRunner
from mcp import Client

from infralink.cli.main import cli
from infralink.mcp_server import create_server
from infralink.operator_surface import operator_surface
from tests.test_public_operation_inventory import RETAINED_OPERATIONS

RETIRED_PUBLIC_PATHS: dict[tuple[str, ...], str] = {
    ("edges-list",): "Use `infralink edge list`.",
    ("hosts",): "Use `infralink host list`.",
    ("services",): "Use `infralink service list`.",
    ("validate",): "Use `infralink fleet validate`.",
}


def _command_argv(operation: str) -> list[str]:
    return [*operation.split("."), "--format", "json"]


def _click_schema_names() -> set[str]:
    return {
        operation.name
        for operation in operator_surface.operations.list()
        if operation.name in RETAINED_OPERATIONS
    }


def _click_payload(output: str) -> dict[str, Any]:
    """Generated parse failures default to YAML before a leaf binds --format."""
    loaded = json.loads(output) if output.lstrip().startswith("{") else yaml.safe_load(output)
    assert isinstance(loaded, dict)
    return loaded


def test_every_retained_operation_has_matching_click_and_mcp_inputs() -> None:
    """Field names and requiredness come only from the registered Pydantic model."""

    async def schemas() -> dict[str, dict[str, Any]]:
        async with Client(create_server()) as client:
            page = await client.list_tools()
            tools = list(page.tools)
            while page.next_cursor is not None:
                page = await client.list_tools(cursor=page.next_cursor)
                tools.extend(page.tools)
        return {tool.name: tool.input_schema for tool in tools}

    mcp_schemas = asyncio.run(schemas())
    assert _click_schema_names() == RETAINED_OPERATIONS == set(mcp_schemas)
    for definition in operator_surface.operations.list():
        fields = definition.input_model.model_fields
        schema = mcp_schemas[definition.name]
        assert set(schema.get("properties", {})) == set(fields)
        assert set(schema.get("required", [])) == {
            name for name, field in fields.items() if field.is_required()
        }


def test_every_retained_operation_has_a_structured_click_and_mcp_invocation() -> None:
    """Incomplete requests are still canonical envelopes on both transports."""

    runner = CliRunner()
    for operation in sorted(RETAINED_OPERATIONS):
        click_result = runner.invoke(cli, _command_argv(operation))
        assert click_result.exit_code in {0, 1, 2, 3, 4}, click_result.output
        click_payload = _click_payload(click_result.output)
        assert click_payload["schema_version"] == "infralink.cli/v1"
        assert click_payload["command"]["parsed"]["path"] == operation.split(".")

    async def invoke() -> dict[str, dict[str, Any]]:
        responses: dict[str, dict[str, Any]] = {}
        async with Client(create_server()) as client:
            for operation in sorted(RETAINED_OPERATIONS):
                response = await client.call_tool(operation, {})
                responses[operation] = response.structured_content
        return responses

    for operation, payload in asyncio.run(invoke()).items():
        assert payload["schema_version"] == "infralink.cli/v1"
        assert payload["command"]["parsed"]["path"] == operation.split(".")


def test_retired_aliases_are_rejected_by_the_generated_root() -> None:
    runner = CliRunner()
    for path in RETIRED_PUBLIC_PATHS:
        result = runner.invoke(cli, [*path, "--format", "json"])
        assert result.exit_code == 2
        assert _click_payload(result.output)["error"]["code"] == "usage_error"


@pytest.mark.xfail(
    strict=True,
    reason="agent-surface#43: direct root option parse errors still bypass its canonical renderer",
)
def test_retired_root_global_selector_grammar_is_rejected_as_a_canonical_envelope() -> None:
    runner = CliRunner()
    for argv in (
        ["--registry", "/registry", "host", "list"],
        ["--edges", "/edges.yml", "edge", "list"],
        ["--output", "json", "host", "list"],
        ["help", "host"],
    ):
        result = runner.invoke(cli, argv)
        assert result.exit_code == 2
        assert _click_payload(result.output)["error"]["code"] == "usage_error"
