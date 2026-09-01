"""Production projections for the read-only V2 topology diagram operation."""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from mcp import Client

import infralink.cli.main as cli_main
import infralink.operator_surface as operator_surface_module
from infralink.cli.main import cli
from infralink.observation.topology_diagrams import V2TopologyRenderBoundsError
from infralink.operator_surface import diagram_mcp_adapter, diagram_surface

HOST_ID = "11111111-1111-4111-8111-111111111111"
OTHER_HOST_ID = "22222222-2222-4222-8222-222222222222"

SOURCE = f"""\
schema_version: infralink.observation/v2
service_profiles:
  - id: relay
    components:
      - id: api
        endpoints:
          - {{id: tcp, protocol: tcp, port: 443}}
      - id: database
        endpoints:
          - {{id: postgres, protocol: tcp, port: 5432}}
service_instances:
  - id: relay
    host_id: {HOST_ID}
    profile_id: relay
    components:
      - {{slot_id: api}}
      - {{slot_id: database}}
component_edges:
  - id: api-to-database
    source_endpoint_id: {HOST_ID}/relay/api/tcp
    target_endpoint_id: {HOST_ID}/relay/database/postgres
"""


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "topology.yml"
    path.write_text(SOURCE, encoding="utf-8")
    return path


def _source_with_nonfocal_service(tmp_path: Path) -> Path:
    document = yaml.safe_load(SOURCE)
    document["service_instances"].append(
        {
            "id": "other",
            "host_id": OTHER_HOST_ID,
            "profile_id": "relay",
            "components": [{"slot_id": "api"}, {"slot_id": "database"}],
        }
    )
    path = tmp_path / "oversized-topology.yml"
    path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    return path


def test_diagram_project_is_one_read_only_agent_surface_operation() -> None:
    definition = diagram_surface.operations.describe("diagram.project")

    assert definition.read_only is True
    assert set(definition.input_model.model_fields) == {
        "source",
        "scope",
        "host",
        "service",
        "syntax",
    }


def test_diagram_project_defaults_to_yaml_and_projects_mermaid(tmp_path: Path) -> None:
    source = _source(tmp_path)

    result = CliRunner().invoke(cli, ["diagram", "project", "--source", str(source)])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["ok"] is True
    assert payload["command"]["parsed"]["path"] == ["diagram", "project"]
    assert payload["result"] == {
        "syntax": "mermaid",
        "scope": "full",
        "resolved_focus": None,
        "node_count": 2,
        "edge_count": 1,
        "source": payload["result"]["source"],
    }
    assert payload["result"]["source"].startswith("flowchart LR\n")
    assert payload["next_actions"] == [
        {
            "rel": "help",
            "command": "infralink help diagram project",
            "description": "Show diagram project help",
            "safe": True,
        }
    ]


def test_diagram_project_never_selects_an_ambient_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)

    def denied_registry_lookup() -> Path:
        raise AssertionError("diagram project must not resolve an ambient registry")

    monkeypatch.setattr(cli_main, "_configured_registry", denied_registry_lookup)

    result = CliRunner().invoke(cli, ["diagram", "project", "--source", str(source)])

    assert result.exit_code == 0, result.output


@pytest.mark.parametrize("option", ["--registry", "--edges"])
def test_diagram_project_ignores_ambient_root_sources_but_rejects_explicit_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, option: str
) -> None:
    source = _source(tmp_path)
    ambient = tmp_path / "ambient.yml"
    monkeypatch.setenv("INFRALINK_REGISTRY", str(ambient))
    monkeypatch.setenv("INFRALINK_EDGES", str(ambient))

    ambient_result = CliRunner().invoke(cli, ["diagram", "project", "--source", str(source)])
    explicit_result = CliRunner().invoke(
        cli,
        ["--output", "json", option, str(ambient), "diagram", "project", "--source", str(source)],
    )

    assert ambient_result.exit_code == 0, ambient_result.output
    ambient_payload = yaml.safe_load(ambient_result.output)
    assert ambient_payload["command"]["resolved"]["registry"] is None
    assert ambient_payload["command"]["resolved"]["edges"] is None
    assert str(ambient) not in json.dumps(ambient_payload)
    assert str(source) in ambient_payload["command"]["raw"]
    explicit_payload = json.loads(explicit_result.output)
    assert explicit_result.exit_code == 2
    assert explicit_payload["error"]["code"] == "diagram_project_forbidden_input"


@pytest.mark.parametrize(
    ("scope", "options", "code"),
    [
        ("full", ["--host", HOST_ID], "diagram_scope_selector_invalid"),
        ("host", [], "diagram_scope_selector_invalid"),
        ("service", ["--host", HOST_ID], "diagram_scope_selector_invalid"),
        ("service", ["--service", "not-qualified"], "diagram_scope_selector_invalid"),
    ],
)
def test_diagram_project_rejects_invalid_scope_selector_combinations(
    tmp_path: Path, scope: str, options: list[str], code: str
) -> None:
    source = _source(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "diagram",
            "project",
            "--source",
            str(source),
            "--scope",
            scope,
            *options,
        ],
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == code


@pytest.mark.parametrize(
    ("scope", "option", "value"),
    [
        ("host", "--host", "not-a-uuid"),
        ("host", "--host", "ABCDEFAB-CDEF-4ABC-8DEF-ABCDEFABCDEF"),
        ("service", "--service", "not-a-uuid/relay"),
        ("service", "--service", f"{HOST_ID}/"),
        ("service", "--service", f"{HOST_ID}/relay/extra"),
        ("service", "--service", f"{HOST_ID}/Relay"),
    ],
)
def test_diagram_project_requires_uuid_qualified_focus_selectors(
    tmp_path: Path, scope: str, option: str, value: str
) -> None:
    source = _source(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "diagram",
            "project",
            "--source",
            str(source),
            "--scope",
            scope,
            option,
            value,
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["code"] == "diagram_scope_selector_invalid"


def test_diagram_project_translates_render_bounds_for_cli_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)

    def bounds_error(*_args: object, **_kwargs: object) -> str:
        raise V2TopologyRenderBoundsError("rendered topology exceeds test limit")

    monkeypatch.setattr(operator_surface_module, "render_v2_mermaid", bounds_error)
    cli_result = CliRunner().invoke(
        cli, ["--output", "json", "diagram", "project", "--source", str(source)]
    )

    async def call_mcp() -> tuple[bool, dict[str, object]]:
        async with Client(diagram_mcp_adapter().server) as client:
            result = await client.call_tool("diagram.project", {"source": [str(source)]})
        return result.is_error, result.structured_content

    is_error, mcp_payload = asyncio.run(call_mcp())
    cli_payload = json.loads(cli_result.output)
    assert cli_result.exit_code == 3
    assert cli_payload["error"]["code"] == "diagram_render_bounds_exceeded"
    assert is_error is True
    assert mcp_payload["error"] == cli_payload["error"]


def test_diagram_project_translates_topology_bounds_for_cli_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import infralink.observation.topology as topology

    source = _source_with_nonfocal_service(tmp_path)
    focused_result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "diagram",
            "project",
            "--source",
            str(source),
            "--scope",
            "host",
            "--host",
            HOST_ID,
        ],
    )
    assert focused_result.exit_code == 0, focused_result.output
    assert json.loads(focused_result.output)["result"]["node_count"] == 2

    # The full declaration contains nine projected items; the valid host focus has five.
    monkeypatch.setattr(topology, "_MAX_TOPOLOGY_ITEMS", 8)
    cli_result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "diagram",
            "project",
            "--source",
            str(source),
            "--scope",
            "host",
            "--host",
            HOST_ID,
        ],
    )

    async def call_mcp() -> tuple[bool, dict[str, object]]:
        async with Client(diagram_mcp_adapter().server) as client:
            result = await client.call_tool(
                "diagram.project",
                {"source": [str(source)], "scope": "host", "host": HOST_ID},
            )
        return result.is_error, result.structured_content

    is_error, mcp_payload = asyncio.run(call_mcp())
    cli_payload = json.loads(cli_result.output)
    assert cli_result.exit_code == 3
    assert cli_payload["error"] == {
        "code": "diagram_topology_bounds_exceeded",
        "message": "V2 topology declaration exceeds the projection item limit",
        "details": {},
    }
    assert (
        cli_payload["fix"]
        == "Reduce or split the full declaration; narrowing diagram focus does not reduce this bound."
    )
    assert is_error is True
    assert mcp_payload["error"] == cli_payload["error"]
    assert mcp_payload["fix"] == cli_payload["fix"]


def test_diagram_project_native_mcp_matches_cli_and_accepts_only_v2_inputs(tmp_path: Path) -> None:
    source = _source(tmp_path)
    cli_result = CliRunner().invoke(
        cli,
        ["--output", "json", "diagram", "project", "--source", str(source), "--syntax", "dot"],
    )

    async def call_mcp() -> tuple[dict[str, object], dict[str, object]]:
        async with Client(diagram_mcp_adapter().server) as client:
            tools = await client.list_tools()
            tool = next(item for item in tools.tools if item.name == "diagram.project")
            result = await client.call_tool(
                "diagram.project", {"source": [str(source)], "syntax": "dot"}
            )
        assert result.is_error is False
        return tool.input_schema, result.structured_content

    assert cli_result.exit_code == 0, cli_result.output
    tool_schema, mcp_payload = asyncio.run(call_mcp())
    cli_payload = json.loads(cli_result.output)
    assert set(tool_schema["properties"]) == {"source", "scope", "host", "service", "syntax"}
    assert tool_schema["properties"]["source"]["type"] == "array"
    assert tool_schema["properties"]["source"]["items"]["type"] == "string"
    assert tool_schema["properties"]["source"]["minItems"] == 1
    assert tool_schema["required"] == ["source"]
    assert mcp_payload["result"] == cli_payload["result"]


def test_diagram_project_local_adapter_exposes_the_typed_diagram_operation() -> None:
    async def list_tools() -> tuple[set[str], dict[str, object]]:
        async with Client(diagram_mcp_adapter().server) as client:
            tools = await client.list_tools()
        diagram = next(tool for tool in tools.tools if tool.name == "diagram.project")
        return {tool.name for tool in tools.tools}, diagram.input_schema

    names, legacy_schema = asyncio.run(list_tools())

    assert names == {"diagram.project"}
    assert "source" in legacy_schema["properties"]
    assert legacy_schema["required"] == ["source"]


def test_diagram_project_denies_side_effects_and_legacy_output_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)

    def denied(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("diagram project must not invoke a side effect")

    monkeypatch.setattr(subprocess, "run", denied)
    monkeypatch.setattr(socket, "create_connection", denied)
    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(Path, "write_text", denied)

    result = CliRunner().invoke(cli, ["diagram", "project", "--source", str(source)])
    output_path = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "diagram",
            "--output",
            str(tmp_path / "output"),
            "project",
            "--source",
            str(source),
        ],
    )

    assert result.exit_code == 0, result.output
    assert output_path.exit_code == 2
    assert json.loads(output_path.output)["error"]["code"] == "diagram_project_forbidden_input"
