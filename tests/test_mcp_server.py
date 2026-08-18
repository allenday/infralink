from __future__ import annotations

import asyncio
import sys

import pytest
import yaml
from click.testing import CliRunner
from mcp import Client, ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from infralink.cli.main import cli
from infralink.mcp_server import create_server, invoke_cli


def test_help_discovers_native_mcp_server_command() -> None:
    result = CliRunner().invoke(cli, ["help", "mcp"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is True
    assert payload["result"]["path"] == ["mcp"]
    assert [child["name"] for child in payload["result"]["children"]] == ["serve"]


def test_mcp_invokes_existing_cli_and_preserves_hateoas_envelope() -> None:
    payload = invoke_cli(["version"])

    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["ok"] is True
    assert payload["command"]["parsed"]["path"] == ["version"]
    assert isinstance(payload["next_actions"], list)


@pytest.mark.parametrize(
    "argv",
    [
        ["--output", "yaml", "version"],
        ["--output=yaml", "version"],
        ["-o", "yaml", "version"],
        ["mcp", "serve"],
        ["-v", "mcp", "serve"],
        ["--registry", "/tmp/registry", "mcp", "serve"],
    ],
)
def test_mcp_rejects_transport_recursive_or_output_overriding_argv(argv: list[str]) -> None:
    with pytest.raises(ValueError):
        invoke_cli(argv)


def test_mcp_protocol_discovers_and_calls_infralink_command() -> None:
    async def exercise_protocol() -> None:
        async with Client(create_server()) as client:
            tools = await client.list_tools()
            assert [tool.name for tool in tools.tools] == [
                "infralink_command",
                "infralink_help",
                "infralink_doctor",
                "infralink_host_get",
                "infralink_host_list",
                "infralink_host_status",
                "infralink_host_logs",
            ]

            result = await client.call_tool("infralink_command", {"argv": ["version"]})
            assert result.is_error is False
            assert result.structured_content["schema_version"] == "infralink.cli/v1"
            assert result.structured_content["command"]["parsed"]["path"] == ["version"]

            help_result = await client.call_tool("infralink_help", {"path": ["host"]})
            assert help_result.is_error is False
            assert help_result.structured_content["command"]["parsed"]["path"] == ["host"]
            assert help_result.structured_content["result"]["path"] == ["host"]

            bad_doctor = await client.call_tool("infralink_doctor", {"target_type": "host"})
            assert bad_doctor.is_error is True
            assert "target_type and target_ref" in bad_doctor.content[0].text

    asyncio.run(exercise_protocol())


def test_native_mcp_serve_command_speaks_stdio_protocol() -> None:
    async def exercise_stdio() -> None:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "infralink", "mcp", "serve"],
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                initialized = await client.initialize()
                assert initialized.server_info.name == "infralink"

                tools = await client.list_tools()
                assert [tool.name for tool in tools.tools] == ["infralink_command"]

    asyncio.run(exercise_stdio())
