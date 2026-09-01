from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import agent_surface
import pytest
import yaml
from click.testing import CliRunner
from mcp import Client, ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from infralink.cli import command_plugins
from infralink.cli.main import cli
from infralink.mcp_server import (
    _arguments,
    _native_argv,
    _native_paths,
    _native_tool,
    _parameter_schema,
    create_server,
    invoke_cli,
)


def test_mcp_allows_command_local_artifact_output_without_root_format_override() -> None:
    argv, stdin = _arguments({"argv": ["analyze", "--output", "artifacts"], "stdin": None})

    assert argv == ["analyze", "--output", "artifacts"]
    assert stdin is None


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
        ["-r", "/registry", "-o", "yaml", "analyze", "--output", "artifacts"],
        ["-e", "/edges.yml", "--output", "yaml", "analyze", "--output", "artifacts"],
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
            names = {tool.name for tool in tools.tools}
            assert "infralink_command" in names
            assert {"infralink_help", "infralink_doctor", "infralink_host_apply"} <= names
            assert "infralink_mcp_serve" not in names

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
            assert bad_doctor.structured_content["schema_version"] == "infralink.cli/v1"

    asyncio.run(exercise_protocol())


def test_native_mcp_preserves_bounded_integer_option_types() -> None:
    async def exercise_protocol() -> None:
        async with Client(create_server()) as client:
            tools = await client.list_tools()
            apply = next(tool for tool in tools.tools if tool.name == "infralink_host_apply")

            timeout = apply.input_schema["properties"]["timeout"]
            assert timeout == {"type": "integer", "minimum": 1, "maximum": 3600}

    asyncio.run(exercise_protocol())


def test_native_mcp_projects_root_topology_sources_before_the_command_path() -> None:
    tool = _native_tool("infralink_version", ("version",))

    assert {"registry", "edges"} <= set(tool.input_schema["properties"])
    assert tool.input_schema["properties"]["registry"]["description"] == "Registry checkout root."
    assert _native_argv(
        "infralink_version",
        {"registry": "/registry", "edges": "/edges.yml"},
    ) == ["--registry", "/registry", "--edges", "/edges.yml", "version"]


def test_native_mcp_keeps_analyze_artifact_options_distinct_from_root_sources() -> None:
    tool = _native_tool("infralink_analyze", ("analyze",))

    assert tool.input_schema["properties"]["registry"]["type"] == "string"
    assert tool.input_schema["properties"]["edges"]["type"] == "string"
    assert tool.input_schema["properties"]["include_edges"] == {"type": "boolean"}
    assert _native_argv(
        "infralink_analyze",
        {
            "registry": "/registry",
            "edges": "/edges.yml",
            "output": "artifacts",
            "include_edges": False,
        },
    ) == [
        "--registry",
        "/registry",
        "--edges",
        "/edges.yml",
        "analyze",
        "--output",
        "artifacts",
        "--no-edges",
    ]


def test_mcp_analyze_uses_a_root_checkout_and_rejects_local_registry_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    checkout = tmp_path / "registry"
    manifest = checkout / "hosts/11111111-1111-4111-8111-111111111111/manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        """hosts:
  11111111-1111-4111-8111-111111111111:
    canonical_name: alpha
    status: active
    roles: [api]
""",
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    async def exercise_protocol() -> None:
        async with Client(create_server()) as client:
            native = await client.call_tool(
                "infralink_analyze",
                {"registry": str(checkout), "output": "artifacts", "include_edges": False},
            )
            legacy = await client.call_tool(
                "infralink_command",
                {
                    "argv": [
                        "--registry",
                        str(checkout),
                        "analyze",
                        "--registry",
                        str(checkout),
                        "--output",
                        "rejected",
                    ]
                },
            )

            assert native.is_error is False
            assert native.structured_content["command"]["resolved"]["registry"] == str(checkout)
            assert legacy.is_error is True
            assert legacy.structured_content["error"]["code"] == "usage_error"

    asyncio.run(exercise_protocol())


def test_native_mcp_help_retains_root_topology_sources() -> None:
    tool = _native_tool("infralink_help", ("help",))

    assert {"path", "registry", "edges"} <= set(tool.input_schema["properties"])
    assert _native_argv("infralink_help", {"registry": "/registry", "path": ["host"]}) == [
        "--registry",
        "/registry",
        "help",
        "host",
    ]


def test_native_mcp_never_overloads_root_topology_source_fields() -> None:
    for name, path in _native_paths().items():
        properties = _native_tool(name, path).input_schema["properties"]
        if path == ("diagram", "project"):
            assert "registry" not in properties
            assert "edges" not in properties
            continue
        assert properties["registry"]["type"] == "string"
        assert properties["edges"]["type"] == "string"


def test_native_mcp_preserves_every_click_integer_parameter_type() -> None:
    from click.types import IntParamType

    from infralink.cli.main import _command_for_path

    with command_plugins.discovery_scope():
        for path in _native_paths().values():
            if command_plugins.operation(path) is not None:
                # External Agent Surface operations are schema-projected from
                # their wheel manifest and intentionally are not imported here.
                continue
            command = _command_for_path(path)
            assert command is not None
            for parameter in command.params:
                if isinstance(parameter.type, IntParamType):
                    assert _parameter_schema(parameter)["type"] == "integer"


def test_native_mcp_returns_a_canonical_usage_envelope_for_invalid_bounded_integer() -> None:
    async def exercise_protocol() -> None:
        async with Client(create_server()) as client:
            result = await client.call_tool(
                "infralink_host_apply",
                {"host_ref": "relayos-staging", "dry_run": True, "timeout": "60s"},
            )

            assert result.is_error is True
            assert result.structured_content["schema_version"] == "infralink.cli/v1"
            assert result.structured_content["error"]["code"] == "usage_error"
            assert result.structured_content["next_actions"] == [
                {
                    "rel": "help",
                    "command": "infralink --output json help host apply",
                    "description": "Show command usage",
                    "safe": True,
                }
            ]

    asyncio.run(exercise_protocol())


def test_native_mcp_serve_command_speaks_stdio_protocol() -> None:
    async def exercise_stdio() -> None:
        agent_surface_source = Path(agent_surface.__file__).resolve().parents[1]
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "infralink", "mcp", "serve"],
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    (
                        str(agent_surface_source),
                        str(Path(__file__).resolve().parents[1] / "src"),
                    )
                ),
            },
        )
        async with stdio_client(parameters) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as client:
                initialized = await client.initialize()
                assert initialized.server_info.name == "infralink"

                tools = await client.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "infralink_command" in names
                assert {"infralink_help", "infralink_doctor", "infralink_host_apply"} <= names
                assert "infralink_mcp_serve" not in names

                status = await client.call_tool(
                    "infralink_host_status", {"host_ref": "missing-host"}
                )
                assert status.is_error is True
                assert status.structured_content["schema_version"] == "infralink.cli/v1"
                assert status.structured_content["error"]["code"] == "configuration_required"

    asyncio.run(exercise_stdio())
