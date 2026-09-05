"""The generated transports must retain Infralink's canonical response envelope."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import pytest
from agent_surface import Invocation, OutputBudget
from agent_surface.contracts import Action, ActionCollection, CommandView, ParsedCommand
from click.testing import CliRunner
from mcp import Client

from infralink.agent_surface import InfralinkEnvelopeRenderer
from infralink.cli.contracts import HostListResult
from infralink.cli.main import cli
from infralink.operator_surface import (
    operator_click_adapter,
    operator_mcp_adapter,
    operator_surface,
)


def test_public_host_help_exposes_the_complete_generated_operation_family() -> None:
    result = CliRunner().invoke(cli, ["help", "--path", "host"])

    assert result.exit_code == 0, result.output
    document = json.loads(
        CliRunner().invoke(cli, ["help", "--path", "host", "--format", "json"]).output
    )
    assert {child["name"] for child in document["result"]["children"]} == {
        "apply",
        "bootstrap",
        "create",
        "list",
        "logs",
        "show",
        "status",
        "verifier",
    }


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
    click_result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["host", "list", "--registry", str(tmp_path), "--format", "json"],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool("host.list", {"registry": str(tmp_path)})
        assert result.is_error is False
        return result.structured_content

    assert click_result.exit_code == 0, click_result.output
    click_document = json.loads(click_result.output)
    mcp_document = asyncio.run(call_mcp())
    for document, output_arg in ((click_document, " --format json"), (mcp_document, "")):
        assert document["schema_version"] == "infralink.cli/v1"
        assert document["ok"] is True
        assert document["command"]["parsed"]["path"] == ["host", "list"]
        assert document["command"]["resolved"]["registry"] == str(tmp_path)
        assert document["result"]["items"] == [host_id]
        assert document["next_actions"] == [
            {
                "rel": "show",
                "command": f"infralink host show '{{host_id}}' --registry {tmp_path}{output_arg}",
                "description": "Show one host declaration",
                "safe": True,
                "templated": True,
                "bindings": {
                    "host_id": {
                        "type": "string",
                        "required": True,
                        "source": "result.items[]",
                    }
                },
            }
        ]

    assert click_document["command"]["raw"] == (
        f"infralink host list --registry {tmp_path} --format json"
    )
    assert click_document["command"]["parsed"]["flags"] == ["--registry", "--format"]
    assert click_document["command"]["resolved"]["output"] == "json"

    assert mcp_document["command"]["raw"] == f"infralink host list --registry {tmp_path}"
    assert mcp_document["command"]["parsed"]["flags"] == ["--registry"]
    assert mcp_document["command"]["resolved"]["output"] == "json"


def test_host_list_actions_pin_the_configured_checkout_for_cli_and_mcp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    manifest = tmp_path / "registry" / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    config = tmp_path / "operator.yml"
    config.write_text(f"registry: {tmp_path / 'registry'}\n", encoding="utf-8")
    monkeypatch.setenv("INFRALINK_CONFIG", str(config))
    monkeypatch.delenv("INFRALINK_REGISTRY", raising=False)
    monkeypatch.delenv("INFRALINK_EDGES", raising=False)

    click_result = CliRunner().invoke(cli, ["host", "list", "--format", "json"])

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool("host.list", {})
        assert result.is_error is False
        return result.structured_content

    assert click_result.exit_code == 0, click_result.output
    documents = ((json.loads(click_result.output), " --format json"), (asyncio.run(call_mcp()), ""))
    for document, output_arg in documents:
        show = next(action for action in document["next_actions"] if action["rel"] == "show")
        assert show["command"] == (
            f"infralink host show '{{host_id}}' --registry {tmp_path / 'registry'}{output_arg}"
        )


def test_direct_mcp_uses_the_documented_environment_edge_selector(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    manifest = tmp_path / "registry" / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    selected_edges = tmp_path / "selected-edges.yml"
    selected_edges.write_text("edges: []\n", encoding="utf-8")
    monkeypatch.setenv("INFRALINK_REGISTRY", str(tmp_path / "registry"))
    monkeypatch.setenv("INFRALINK_EDGES", str(selected_edges))

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool("edge.list", {})
        assert result.is_error is False
        return result.structured_content

    document = asyncio.run(call_mcp())
    assert document["command"]["resolved"]["registry"] == str(tmp_path / "registry")
    assert document["command"]["resolved"]["edges"] == str(selected_edges)


def test_mixed_cli_registry_and_environment_edges_stay_replayable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    selected_edges = tmp_path / "selected-edges.yml"
    selected_edges.write_text("edges: []\n", encoding="utf-8")
    monkeypatch.setenv("INFRALINK_EDGES", str(selected_edges))

    result = CliRunner().invoke(
        cli,
        ["host", "list", "--registry", str(registry), "--format", "json"],
    )

    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    show = next(action for action in document["next_actions"] if action["rel"] == "show")
    assert show["command"] == (
        f"infralink host show '{{host_id}}' --registry {registry} --edges {selected_edges} --format json"
    )


def test_direct_mcp_resolves_relative_environment_edge_actions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    selected_edges = tmp_path / "selected-edges.yml"
    selected_edges.write_text("edges: []\n", encoding="utf-8")
    monkeypatch.setenv("INFRALINK_REGISTRY", str(registry))
    monkeypatch.setenv("INFRALINK_EDGES", os.path.relpath(selected_edges, Path.cwd()))

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool("host.list", {})
        assert result.is_error is False
        return result.structured_content

    document = asyncio.run(call_mcp())
    show = next(action for action in document["next_actions"] if action["rel"] == "show")
    assert f"--edges {selected_edges}" in show["command"]


def test_host_logs_is_a_typed_operation_with_the_diagnostic_parameter() -> None:
    """The diagnostic query is declared once, rather than reflected from Click."""
    definition = operator_surface.operations.describe("host.logs")

    assert definition.input_model.model_fields["host_ref"].is_required()
    assert definition.input_model.model_fields["last_run"].is_required()
    assert "diagnostic" in definition.input_model.model_fields

    async def listed_schema() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "host.logs")
        return tool.input_schema

    schema = asyncio.run(listed_schema())
    diagnostic = schema["properties"]["diagnostic"]
    assert diagnostic["default"] is False
    assert diagnostic["type"] == "boolean"
    assert set(schema["required"]) == {"host_ref", "last_run"}
    assert schema["properties"]["registry"]["default"] is None


def test_host_control_operations_are_all_declared_by_the_agent_surface() -> None:
    """The complete host family is declared once, without a parallel Click tree."""
    expected = {
        "host.apply",
        "host.bootstrap",
        "host.create",
        "host.list",
        "host.logs",
        "host.show",
        "host.status",
        "host.verifier",
    }

    assert expected <= {definition.name for definition in operator_surface.operations.list()}


def test_edge_resolution_and_health_operations_are_declared_once() -> None:
    """Public edge reads must not retain an independent Click parser."""
    declared = {definition.name for definition in operator_surface.operations.list()}

    assert {"check", "resolve"} <= declared


def test_analyze_is_a_typed_explicit_artifact_write_operation() -> None:
    """Analyze owns its output path but inherits source selection from the root."""
    definition = operator_surface.operations.describe("analyze")

    assert definition.read_only is False
    assert definition.input_model.model_fields["output"].is_required()
    assert {
        "include_edges",
        "include_diagram",
        "include_monitoring",
    } <= set(definition.input_model.model_fields)

    async def listed_schema() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "analyze")
        return tool.input_schema

    schema = asyncio.run(listed_schema())
    assert set(schema["required"]) == {"output"}
    assert schema["properties"]["registry"]["default"] is None


def test_docs_is_a_typed_explicit_artifact_write_operation() -> None:
    """Docs owns its output path and inherits topology sources from the root."""
    definition = operator_surface.operations.describe("docs")

    assert definition.read_only is False
    assert definition.input_model.model_fields["output"].is_required()
    assert {"host", "index_only", "document_format"} <= set(definition.input_model.model_fields)

    async def listed_schema() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            tools = await client.list_tools()
        tool = next(item for item in tools.tools if item.name == "docs")
        return tool.input_schema

    schema = asyncio.run(listed_schema())
    assert set(schema["required"]) == {"output"}
    assert schema["properties"]["registry"]["default"] is None


def test_diagram_is_a_typed_explicit_artifact_write_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Diagram generation has one Click/MCP operation and no legacy callback."""
    definition = operator_surface.operations.describe("diagram")

    assert definition.read_only is False
    assert definition.input_model.model_fields["output"].is_required()
    assert {"diagram_format", "group", "include_terminated"} <= set(
        definition.input_model.model_fields
    )

    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: active\n",
        encoding="utf-8",
    )
    edges = registry / "edges.yml"
    edges.write_text("edges: []\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    click_result = CliRunner().invoke(
        cli,
        [
            "diagram",
            "--registry",
            str(registry),
            "--edges",
            str(edges),
            "--output",
            "artifacts",
            "--diagram-format",
            "all",
            "--limit",
            "1",
            "--format",
            "json",
        ],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool(
                "diagram",
                {
                    "registry": str(registry),
                    "edges": str(edges),
                    "output": "artifacts",
                    "diagram_format": "all",
                    "limit": 1,
                },
            )
        assert result.is_error is False
        return result.structured_content

    assert click_result.exit_code == 0, click_result.output
    click_document = json.loads(click_result.output)
    mcp_document = asyncio.run(call_mcp())
    assert click_document["result"] == mcp_document["result"]
    assert [item["path"] for item in click_document["result"]["artifacts"]["items"]] == [
        "artifacts/infrastructure.d2"
    ]
    continuation = click_document["next_actions"][0]
    assert continuation["rel"] == "continue"
    assert continuation["command"].startswith(
        f"infralink diagram --output artifacts --diagram-format all --collection artifacts --cursor '{{cursor}}' --limit 1 --registry {registry} --edges {edges}"
    )


def test_renderer_projects_concrete_hateoas_actions_into_the_v1_normal_form() -> None:
    definition = operator_surface.operations.describe("host.list")
    renderer = InfralinkEnvelopeRenderer()

    document = renderer.render(
        Invocation(
            operation=definition,
            request={"registry": "/registry"},
            result=HostListResult(items=[]),
            error=None,
            next_actions=ActionCollection(
                items=(
                    Action(
                        rel="next",
                        description="Inspect the next page",
                        command=("host", "list"),
                        operation="host.list",
                    ),
                ),
                total=1,
                returned=1,
            ),
            budget=OutputBudget(),
        )
    )

    assert [action.model_dump(mode="json") for action in document.next_actions] == [
        {
            "rel": "next",
            "command": "infralink host list --registry /registry",
            "description": "Inspect the next page",
            "safe": True,
        }
    ]


def test_renderer_prefixes_a_mounted_plugin_command_with_infralink() -> None:
    definition = operator_surface.operations.describe("host.list")
    document = InfralinkEnvelopeRenderer().render(
        Invocation(
            operation=definition,
            request={},
            result=HostListResult(items=[]),
            error=None,
            command=CommandView(
                raw=("controller", "doctor"),
                parsed=ParsedCommand(path=("controller", "doctor")),
            ),
            next_actions=ActionCollection(),
            budget=OutputBudget(),
        )
    )

    assert document.command.raw == "infralink controller doctor"


def test_renderer_fails_closed_for_unresolved_hateoas_action_templates() -> None:
    definition = operator_surface.operations.describe("host.list")

    with pytest.raises(ValueError, match="unresolved action template"):
        InfralinkEnvelopeRenderer().render(
            Invocation(
                operation=definition,
                request={"registry": "/registry"},
                result=HostListResult(items=[]),
                error=None,
                next_actions=ActionCollection(
                    items=(
                        Action(
                            rel="inspect",
                            description="Inspect a host",
                            command_template=("host", "show", "{host_id}"),
                            operation="host.list",
                            slots={"host_id": {"required": True}},
                        ),
                    ),
                    total=1,
                    returned=1,
                ),
                budget=OutputBudget(),
            )
        )


def test_renderer_rejects_actions_whose_command_does_not_match_the_operation() -> None:
    definition = operator_surface.operations.describe("host.list")

    with pytest.raises(ValueError, match="does not match"):
        InfralinkEnvelopeRenderer().render(
            Invocation(
                operation=definition,
                request={"registry": "/registry"},
                result=HostListResult(items=[]),
                error=None,
                next_actions=ActionCollection(
                    items=(
                        Action(
                            rel="apply",
                            description="Mutate a host",
                            command=("host", "bootstrap", "host-1"),
                            operation="host.list",
                        ),
                    ),
                    total=1,
                    returned=1,
                ),
                budget=OutputBudget(),
            )
        )


def test_renderer_rejects_actions_with_conflicting_explicit_sources() -> None:
    definition = operator_surface.operations.describe("host.list")

    with pytest.raises(ValueError, match="source conflicts"):
        InfralinkEnvelopeRenderer().render(
            Invocation(
                operation=definition,
                request={"registry": "/registry"},
                result=HostListResult(items=[]),
                error=None,
                next_actions=ActionCollection(
                    items=(
                        Action(
                            rel="next",
                            description="Inspect the next page",
                            command=("host", "list", "--registry", "/other"),
                            operation="host.list",
                        ),
                    ),
                    total=1,
                    returned=1,
                ),
                budget=OutputBudget(),
            )
        )


def test_renderer_fails_closed_for_truncated_hateoas_action_frontiers() -> None:
    definition = operator_surface.operations.describe("host.list")

    with pytest.raises(ValueError, match="truncated action frontier"):
        InfralinkEnvelopeRenderer().render(
            Invocation(
                operation=definition,
                request={"registry": "/registry"},
                result=HostListResult(items=[]),
                error=None,
                next_actions=ActionCollection(
                    items=(
                        Action(
                            rel="next",
                            description="Inspect the next page",
                            command=("host", "list"),
                            operation="host.list",
                        ),
                    ),
                    total=2,
                    returned=1,
                    truncated=True,
                    discover=Action(
                        rel="discover",
                        description="Discover remaining actions",
                        command=("actions", "list"),
                    ),
                ),
                budget=OutputBudget(),
            )
        )


@pytest.mark.parametrize(
    ("path", "operation", "expected_result", "expected_action_rels"),
    [
        (["service", "list"], "service.list", {"items": ["api", "nginx"]}, ([], [])),
        (
            ["edge", "list"],
            "edge.list",
            {"items": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"]},
            ([], []),
        ),
        (
            ["info"],
            "info",
            {"summary": {"host_count": 1, "service_count": 2, "edge_count": 1}},
            (["list", "list"], ["list", "list"]),
        ),
    ],
)
def test_remaining_typed_reads_project_one_envelope_through_click_and_mcp(
    tmp_path: Path,
    path: list[str],
    operation: str,
    expected_result: dict[str, object],
    expected_action_rels: tuple[list[str], list[str]],
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    manifest = tmp_path / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "\n".join(
            [
                "hosts:",
                f"  {host_id}:",
                "    canonical_name: host-1",
                "    status: active",
                "    roles: [nginx]",
                "    services:",
                "      api:",
                "        port: 8080",
                "        protocol: http",
                "",
            ]
        ),
        encoding="utf-8",
    )
    edges = tmp_path / "network" / "main-dev" / "edges" / "edges.yml"
    edges.parent.mkdir(parents=True)
    edges.write_text(
        "\n".join(
            [
                "edges:",
                "  - id: aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "    type: api",
                "    from:",
                f"      hosts: [{host_id}]",
                "      service: api",
                "    to:",
                f"      host: {host_id}",
                "      service: nginx",
                "      port: 80",
                "    protocol: http",
                "",
            ]
        ),
        encoding="utf-8",
    )
    click_result = CliRunner().invoke(
        operator_click_adapter().command(),
        [*path, "--registry", str(tmp_path), "--edges", str(edges), "--format", "json"],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool(
                operation, {"registry": str(tmp_path), "edges": str(edges)}
            )
        assert result.is_error is False
        return result.structured_content

    assert click_result.exit_code == 0, click_result.output
    click_document = json.loads(click_result.output)
    mcp_document = asyncio.run(call_mcp())
    for document, expected_actions in zip(
        (click_document, mcp_document), expected_action_rels, strict=True
    ):
        assert document["schema_version"] == "infralink.cli/v1"
        assert document["ok"] is True
        assert document["command"]["parsed"]["path"] == path
        assert document["command"]["resolved"]["registry"] == str(tmp_path)
        assert document["command"]["resolved"]["edges"] == str(edges)
        for key, value in expected_result.items():
            assert document["result"][key] == value
        assert [action["rel"] for action in document["next_actions"]] == expected_actions

    assert click_document["result"] == mcp_document["result"]
    assert mcp_document["command"]["raw"] == (
        f"infralink {' '.join(path)} --registry {tmp_path} --edges {edges}"
    )


def test_typed_source_failure_projects_one_error_envelope_through_click_and_mcp(
    tmp_path: Path,
) -> None:
    missing_registry = tmp_path / "missing"
    click_result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["host", "list", "--registry", str(missing_registry), "--format", "json"],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            result = await client.call_tool("host.list", {"registry": str(missing_registry)})
        assert result.is_error is True
        return result.structured_content

    assert click_result.exit_code != 0
    click_document = json.loads(click_result.output)
    mcp_document = asyncio.run(call_mcp())
    for document in (click_document, mcp_document):
        assert document["schema_version"] == "infralink.cli/v1"
        assert document["ok"] is False
        assert document["command"]["parsed"]["path"] == ["host", "list"]
        assert document["error"]["code"] == "input_load_failed"
        assert document["error"]["details"] == {
            "source": "registry",
            "path": str(missing_registry),
        }
        assert document["next_actions"] == []

    assert click_document["error"] == mcp_document["error"]
