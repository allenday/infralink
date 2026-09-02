"""Public Agent Surface cutover coverage for the info operation."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import yaml
from click.testing import CliRunner
from mcp import Client

from infralink.cli.main import cli
from infralink.operator_surface import info_click_command, operator_mcp_adapter, operator_surface


def _registry(tmp_path: Path) -> Path:
    host_id = "11111111-1111-4111-8111-111111111111"
    host = tmp_path / "hosts" / host_id
    host.mkdir(parents=True)
    (host / "manifest.yml").write_text(
        "\n".join(
            (
                "hosts:",
                f"  {host_id}:",
                "    canonical_name: info-host",
                "    status: active",
                "    services:",
                "      api:",
                "        port: 8080",
                "        protocol: http",
                "",
            )
        ),
        encoding="utf-8",
    )
    edges = tmp_path / "network/main-dev/edges"
    edges.mkdir(parents=True)
    (edges / "edges.yml").write_text("edges: []\n", encoding="utf-8")
    return tmp_path


def test_info_click_and_mcp_share_the_registered_operation_and_actions(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    assert info_click_command().name == "info"
    assert operator_surface.operations.describe("info").read_only is True

    cli_result = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "--output", "json", "info"],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            tools = await client.list_tools()
            assert "info" in {tool.name for tool in tools.tools}
            result = await client.call_tool("info", {"registry": str(registry)})
        assert result.is_error is False
        return result.structured_content

    assert cli_result.exit_code == 0, cli_result.output
    cli_payload = json.loads(cli_result.output)
    mcp_payload = asyncio.run(call_mcp())
    assert cli_payload["command"]["raw"] == (f"infralink --registry {registry} --output json info")
    assert cli_payload["command"]["resolved"]["output"] == "json"
    assert cli_payload["command"]["resolved"]["registry"] == str(registry)
    assert cli_payload["result"] == mcp_payload["result"]
    assert cli_payload["next_actions"] == mcp_payload["next_actions"]
    assert [item["command"] for item in cli_payload["next_actions"]] == [
        f"infralink --registry {registry} --edges {registry / 'network/main-dev/edges/edges.yml'} host list",
        f"infralink --registry {registry} --edges {registry / 'network/main-dev/edges/edges.yml'} edge list",
    ]


def test_info_source_failure_is_a_typed_input_error(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    cli_result = CliRunner().invoke(cli, ["--registry", str(missing), "info"])

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool("info", {"registry": str(missing)})
        assert result.is_error is True
        return result.structured_content

    assert cli_result.exit_code == 3
    cli_payload = yaml.safe_load(cli_result.output)
    mcp_payload = asyncio.run(call_mcp())
    for payload in (cli_payload, mcp_payload):
        assert payload["error"]["code"] == "input_load_failed"
        assert payload["error"]["details"] == {"source": "registry", "path": str(missing)}


def test_info_actions_replay_explicit_edge_source(tmp_path: Path) -> None:
    registry = _registry(tmp_path / "registry")
    edges = tmp_path / "edges.yml"
    edges.write_text("edges: []\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "--edges", str(edges), "info"],
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert all(str(edges) in item["command"] for item in payload["next_actions"])
