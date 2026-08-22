"""The generated transports must retain Infralink's canonical response envelope."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agent_surface import Invocation, OutputBudget
from agent_surface.adapters.click import ClickAdapter
from agent_surface.adapters.mcp import MCPAdapter
from agent_surface.contracts import Action, ActionCollection
from click.testing import CliRunner
from mcp import Client

from infralink.agent_surface import InfralinkEnvelopeRenderer
from infralink.operator_surface import operator_surface


def test_host_list_projects_the_same_infralink_envelope_through_click_and_mcp(
    tmp_path: Path,
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    manifest = tmp_path / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    renderer = InfralinkEnvelopeRenderer()

    click_result = CliRunner().invoke(
        ClickAdapter(operator_surface, envelope_renderer=renderer).command(),
        ["host", "list", "--registry", str(tmp_path), "--format", "json"],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(
            MCPAdapter(operator_surface, envelope_renderer=renderer).server
        ) as client:
            result = await client.call_tool("host.list", {"registry": str(tmp_path)})
        assert result.is_error is False
        return result.structured_content

    assert click_result.exit_code == 0, click_result.output
    click_document = json.loads(click_result.output)
    mcp_document = asyncio.run(call_mcp())
    for document in (click_document, mcp_document):
        assert document["schema_version"] == "infralink.cli/v1"
        assert document["ok"] is True
        assert document["command"]["parsed"]["path"] == ["host", "list"]
        assert document["command"]["resolved"]["registry"] == str(tmp_path)
        assert document["result"]["items"] == [host_id]
        assert document["next_actions"] == []

    assert click_document["command"]["raw"] == (
        f"infralink host list --registry {tmp_path} --format json"
    )
    assert click_document["command"]["parsed"]["flags"] == ["--registry", "--format"]
    assert click_document["command"]["resolved"]["output"] == "json"

    assert mcp_document["command"]["raw"] == f"infralink --registry {tmp_path} host list"
    assert mcp_document["command"]["parsed"]["flags"] == ["--registry"]
    assert mcp_document["command"]["resolved"]["output"] == "json"


def test_renderer_fails_closed_until_hateoas_actions_are_projected() -> None:
    definition = operator_surface.operations.describe("host.list")
    renderer = InfralinkEnvelopeRenderer()

    with pytest.raises(ValueError, match="action projection"):
        renderer.render(
            Invocation(
                operation=definition,
                request={"registry": "/registry"},
                result=None,
                error=None,
                next_actions=ActionCollection(
                    items=(
                        Action(
                            rel="next",
                            description="Inspect the next page",
                            command=("infralink", "host", "list"),
                        ),
                    ),
                    total=1,
                    returned=1,
                ),
                budget=OutputBudget(),
            )
        )
