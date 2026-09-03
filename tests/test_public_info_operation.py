"""Topology summary uses the canonical public projections."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from click.testing import CliRunner
from mcp import Client

from infralink.cli.main import cli
from infralink.mcp_server import create_server


def test_info_has_click_and_mcp_parity(tmp_path: Path) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    manifest = tmp_path / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    edges = tmp_path / "topology" / "production" / "edges.yml"
    edges.parent.mkdir(parents=True)
    edges.write_text("edges: []\n", encoding="utf-8")

    cli_result = CliRunner().invoke(cli, ["info", "--registry", str(tmp_path), "--format", "json"])
    assert cli_result.exit_code == 0, cli_result.output

    async def exercise() -> dict[str, object]:
        async with Client(create_server()) as client:
            result = await client.call_tool("info", {"registry": str(tmp_path)})
        assert result.is_error is False
        return result.structured_content

    assert asyncio.run(exercise())["result"] == json.loads(cli_result.output)["result"]
