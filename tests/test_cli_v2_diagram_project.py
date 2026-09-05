"""V2 topology diagrams use the canonical public projections."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner
from mcp import Client

from infralink.cli.main import cli
from infralink.mcp_server import create_server


def test_topology_diagram_has_click_and_mcp_parity(tmp_path: Path) -> None:
    source = tmp_path / "topology.yml"
    source.write_text(
        "schema_version: infralink.observation/v2\n"
        "service_profiles: []\nservice_instances: []\ncomponent_edges: []\n",
        encoding="utf-8",
    )
    cli_result = CliRunner().invoke(
        cli,
        ["topology", "diagram", "--source", str(source), "--format", "json"],
    )
    assert cli_result.exit_code == 0, cli_result.output

    async def exercise() -> dict[str, object]:
        async with Client(create_server()) as client:
            result = await client.call_tool("topology.diagram", {"source": [str(source)]})
        assert result.is_error is False
        return result.structured_content

    assert asyncio.run(exercise())["result"] == json.loads(cli_result.output)["result"]
