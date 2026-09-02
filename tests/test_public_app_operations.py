"""Public Agent Surface cutover coverage for the app read-only family."""

from __future__ import annotations

import asyncio
import copy
import json
import shlex
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from mcp import Client
from scripts.generate_cli_schemas import render_schemas

from infralink.cli.main import cli
from infralink.operator_surface import app_click_command, app_mcp_adapter, app_surface

_PUBLIC_APP_OPERATIONS = {
    "app.list": {
        "cli_path": ("app", "list"),
        "schema": "app-list.json",
        "result_definition": "AppListResult",
        "required_result_fields": {"items"},
    },
    "app.show": {
        "cli_path": ("app", "show"),
        "schema": "app-show.json",
        "result_definition": "AppShowResult",
        "required_result_fields": {"app", "services", "edges"},
    },
}


def _registry(tmp_path: Path) -> Path:
    host_id = "11111111-1111-4111-8111-111111111111"
    host = tmp_path / "hosts" / host_id
    host.mkdir(parents=True)
    (host / "manifest.yml").write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: app-host\n    status: active\n",
        encoding="utf-8",
    )
    edges = tmp_path / "network/main-dev/edges"
    edges.mkdir(parents=True)
    (edges / "edges.yml").write_text("edges: []\n", encoding="utf-8")
    (tmp_path / "hosts" / "applications.yml").write_text(
        "applications:\n  relay:\n    members:\n      - host: " + host_id + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _paged_registry(tmp_path: Path) -> Path:
    registry = _registry(tmp_path)
    host_id = "11111111-1111-4111-8111-111111111111"
    (registry / "hosts" / host_id / "manifest.yml").write_text(
        f"""hosts:
  {host_id}:
    canonical_name: app-host
    status: active
    services:
      alpha: {{port: 10001, protocol: http}}
      beta: {{port: 10002, protocol: http}}
""",
        encoding="utf-8",
    )
    (registry / "hosts" / "applications.yml").write_text(
        f"""applications:
  relay:
    members:
      - host: {host_id}
        services: [alpha, beta]
""",
        encoding="utf-8",
    )
    return registry


def test_typed_app_family_has_strict_cli_registry_and_mcp_bijection() -> None:
    click_context = click.Context(app_click_command())
    cli_leaves = {("app", child) for child in app_click_command().list_commands(click_context)}
    registry_operations = {item.name for item in app_surface.operations.list()}

    async def list_tools() -> dict[str, object]:
        async with Client(app_mcp_adapter().server) as client:
            tools = await client.list_tools()
        return {tool.name: tool.input_schema for tool in tools.tools}

    schemas = asyncio.run(list_tools())
    assert cli_leaves == {item["cli_path"] for item in _PUBLIC_APP_OPERATIONS.values()}
    assert registry_operations == set(_PUBLIC_APP_OPERATIONS)
    assert set(schemas) == registry_operations
    assert schemas["app.list"].get("required", []) == []
    assert schemas["app.show"]["required"] == ["app_id"]
    assert {"registry", "edges", "app_id"} <= set(schemas["app.show"]["properties"])


def test_typed_app_family_output_schemas_are_generated_and_require_typed_results(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    cli_result = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "--output", "json", "app", "list"],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(app_mcp_adapter().server) as client:
            result = await client.call_tool(
                "app.show", {"registry": str(registry), "app_id": "relay"}
            )
        assert result.is_error is False
        return result.structured_content

    assert cli_result.exit_code == 0, cli_result.output
    documents = {
        "app.list": json.loads(cli_result.output),
        "app.show": asyncio.run(call_mcp()),
    }
    rendered = render_schemas()
    schema_dir = Path("src/infralink/schemas/cli/v1")

    for operation, contract in _PUBLIC_APP_OPERATIONS.items():
        filename = contract["schema"]
        schema = json.loads((schema_dir / filename).read_text(encoding="utf-8"))
        assert rendered[filename] == (schema_dir / filename).read_text(encoding="utf-8")
        assert (
            set(schema["$defs"][contract["result_definition"]]["required"])
            == contract["required_result_fields"]
        )
        validator = Draft202012Validator(schema)
        assert validator.is_valid(documents[operation])

        missing_result = copy.deepcopy(documents[operation])
        result = missing_result["result"]
        assert isinstance(result, dict)
        result.pop(next(iter(contract["required_result_fields"])))
        assert not validator.is_valid(missing_result)


def test_typed_app_cli_yaml_and_mcp_structured_json_share_a_registry_fixture(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    cli_result = CliRunner().invoke(cli, ["--registry", str(registry), "app", "list"])

    async def call_mcp() -> dict[str, object]:
        async with Client(app_mcp_adapter().server) as client:
            result = await client.call_tool(
                "app.show", {"registry": str(registry), "app_id": "relay"}
            )
        assert result.is_error is False
        return result.structured_content

    assert cli_result.exit_code == 0, cli_result.output
    cli_payload = yaml.safe_load(cli_result.output)
    mcp_payload = asyncio.run(call_mcp())
    assert cli_payload["command"]["parsed"]["path"] == ["app", "list"]
    assert cli_payload["result"]["items"] == ["relay"]
    assert mcp_payload["command"]["parsed"]["path"] == ["app", "show"]
    assert mcp_payload["result"]["app"]["id"] == "relay"
    assert json.dumps(mcp_payload)


def test_public_app_source_failure_preserves_the_legacy_input_load_contract(
    tmp_path: Path,
) -> None:
    missing_registry = tmp_path / "missing"
    cli_result = CliRunner().invoke(cli, ["--registry", str(missing_registry), "app", "list"])

    async def call_mcp() -> dict[str, object]:
        async with Client(app_mcp_adapter().server) as client:
            result = await client.call_tool("app.list", {"registry": str(missing_registry)})
        assert result.is_error is True
        return result.structured_content

    assert cli_result.exit_code == 3
    cli_payload = yaml.safe_load(cli_result.output)
    mcp_payload = asyncio.run(call_mcp())
    for payload in (cli_payload, mcp_payload):
        assert payload["error"]["code"] == "input_load_failed"
        assert payload["error"]["details"] == {
            "source": "registry",
            "path": str(missing_registry),
        }


@pytest.mark.parametrize("source", ("hosts", "registry.yml"))
def test_typed_app_reads_reject_non_checkout_registry_sources(tmp_path: Path, source: str) -> None:
    registry = _registry(tmp_path)
    target = registry / source
    if target.suffix:
        target.write_text("hosts: {}\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["--registry", str(target), "app", "list"])

    assert result.exit_code == 3
    payload = yaml.safe_load(result.output)
    assert payload["error"]["code"] == "input_load_failed"
    assert payload["error"]["details"] == {"source": "registry", "path": str(target)}


def test_typed_app_actions_canonicalize_explicit_tilde_edge_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    registry = _registry(tmp_path / "registry")
    edges = home / "edges.yml"
    edges.parent.mkdir(parents=True)
    edges.write_text("edges: []\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))

    result = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "--edges", "~/edges.yml", "app", "list"],
    )

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["command"]["resolved"]["edges"] == str(edges)
    show = next(action for action in payload["next_actions"] if action["rel"] == "show")
    assert str(edges) in show["command"]
    assert "~" not in show["command"]


def test_public_app_actions_are_concrete_safe_and_replay_with_inferred_sources(
    tmp_path: Path,
) -> None:
    registry = _paged_registry(tmp_path)
    inferred_edges = registry / "network/main-dev/edges/edges.yml"
    cli_result = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "app", "list"],
    )

    async def call_mcp() -> tuple[dict[str, object], dict[str, object]]:
        async with Client(app_mcp_adapter().server) as client:
            listed = await client.call_tool("app.list", {"registry": str(registry)})
            shown = await client.call_tool(
                "app.show", {"registry": str(registry), "app_id": "relay", "limit": 1}
            )
        assert listed.is_error is False
        assert shown.is_error is False
        return listed.structured_content, shown.structured_content

    assert cli_result.exit_code == 0, cli_result.output
    cli_payload = yaml.safe_load(cli_result.output)
    mcp_list, mcp_show = asyncio.run(call_mcp())
    for payload in (cli_payload, mcp_list):
        actions = {action["rel"]: action for action in payload["next_actions"]}
        assert actions["help"]["safe"] is True
        assert actions["show"]["safe"] is True
        assert str(inferred_edges) in actions["show"]["command"]
        replay = shlex.split(actions["show"]["command"].replace("{app_id}", "relay"))[1:]
        replay_result = CliRunner().invoke(cli, replay)
        assert replay_result.exit_code == 0, replay_result.output

    continuation = next(
        action for action in mcp_show["next_actions"] if action["rel"] == "continue"
    )
    assert continuation["safe"] is True
    assert str(inferred_edges) in continuation["command"]
    assert "--collection services" in continuation["command"]
    replay = shlex.split(continuation["command"])[1:]
    replay_result = CliRunner().invoke(cli, replay)
    assert replay_result.exit_code == 0, replay_result.output


def test_public_app_provenance_resolves_inferred_edges_for_cli_and_mcp(tmp_path: Path) -> None:
    registry = _registry(tmp_path)
    inferred_edges = str(registry / "network/main-dev/edges/edges.yml")
    cli_result = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "--verbose", "app", "list"],
    )

    async def call_mcp() -> dict[str, object]:
        async with Client(app_mcp_adapter().server) as client:
            result = await client.call_tool("app.list", {"registry": str(registry)})
        assert result.is_error is False
        return result.structured_content

    assert cli_result.exit_code == 0, cli_result.output
    cli_resolved = yaml.safe_load(cli_result.output)["command"]["resolved"]
    mcp_resolved = asyncio.run(call_mcp())["command"]["resolved"]
    for resolved in (cli_resolved, mcp_resolved):
        assert resolved["registry"] == str(registry)
        assert resolved["edges"] == inferred_edges
        assert resolved["cwd"] == str(Path.cwd())
    assert cli_resolved["verbose"] is True
    assert mcp_resolved["verbose"] is False


def test_public_app_actions_replay_with_a_nonstandard_declared_edge_companion(
    tmp_path: Path,
) -> None:
    registry = _paged_registry(tmp_path)
    legacy_edges = registry / "network/main-dev/edges/edges.yml"
    declared_edges = registry / "topology/production/edges/edges.yml"
    declared_edges.parent.mkdir(parents=True)
    declared_edges.write_text(legacy_edges.read_text(encoding="utf-8"), encoding="utf-8")
    legacy_edges.unlink()

    result = CliRunner().invoke(cli, ["--registry", str(registry), "app", "list"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["command"]["resolved"]["edges"] == str(declared_edges)
    show = next(action for action in payload["next_actions"] if action["rel"] == "show")
    assert str(declared_edges) in show["command"]
    replay = shlex.split(show["command"].replace("{app_id}", "relay"))[1:]
    replay_result = CliRunner().invoke(cli, replay)
    assert replay_result.exit_code == 0, replay_result.output

    async def call_mcp() -> dict[str, object]:
        async with Client(app_mcp_adapter().server) as client:
            response = await client.call_tool("app.list", {"registry": str(registry)})
        assert response.is_error is False
        return response.structured_content

    mcp_payload = asyncio.run(call_mcp())
    assert mcp_payload["command"]["resolved"]["edges"] == str(declared_edges)
    mcp_show = next(action for action in mcp_payload["next_actions"] if action["rel"] == "show")
    assert str(declared_edges) in mcp_show["command"]


def test_public_app_configured_sources_are_resolved_and_actions_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _paged_registry(tmp_path / "registry")
    config = tmp_path / "operator.yml"
    config.write_text(f"registry: {registry}\n", encoding="utf-8")
    monkeypatch.setenv("INFRALINK_CONFIG", str(config))

    result = CliRunner().invoke(cli, ["app", "show", "relay", "--limit", "1"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    assert payload["command"]["resolved"]["registry"] == str(registry)
    assert payload["command"]["resolved"]["edges"] == str(
        registry / "network/main-dev/edges/edges.yml"
    )
    continuation = next(action for action in payload["next_actions"] if action["rel"] == "continue")
    replay = shlex.split(continuation["command"])[1:]
    replay_result = CliRunner().invoke(cli, replay)
    assert replay_result.exit_code == 0, replay_result.output


def test_public_app_list_show_action_is_untruncated_and_truthfully_templated(
    tmp_path: Path,
) -> None:
    registry = _registry(tmp_path)
    host_id = "11111111-1111-4111-8111-111111111111"
    applications = "\n".join(
        f"  app-{index:02d}:\n    members:\n      - host: {host_id}" for index in range(25)
    )
    (registry / "hosts" / "applications.yml").write_text(
        f"applications:\n{applications}\n", encoding="utf-8"
    )

    result = CliRunner().invoke(cli, ["--registry", str(registry), "app", "list"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.output)
    show_actions = [action for action in payload["next_actions"] if action["rel"] == "show"]
    assert len(show_actions) == 1
    show = show_actions[0]
    assert show["templated"] is True
    assert show["bindings"] == {
        "app_id": {"type": "string", "required": True, "source": "result.items[]"}
    }
    assert "{app_id}" in show["command"]


@pytest.mark.parametrize(
    ("registry_argument", "expected_exit"),
    [(None, 2), ("missing", 3)],
)
def test_public_app_failure_preserves_input_exit_and_help_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    registry_argument: str | None,
    expected_exit: int,
) -> None:
    monkeypatch.delenv("INFRALINK_CONFIG", raising=False)
    arguments = ["app", "list"]
    if registry_argument is not None:
        arguments = ["--registry", str(tmp_path / registry_argument), *arguments]

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == expected_exit
    payload = yaml.safe_load(result.output)
    assert payload["error"]["code"] in {"configuration_required", "input_load_failed"}
    help_action = next(action for action in payload["next_actions"] if action["rel"] == "help")
    assert help_action["safe"] is True
    assert help_action["command"].endswith("help app list")


def test_mcp_migration_inventory_defers_public_cutover_until_full_projection() -> None:
    inventory = Path("docs/mcp-migration-inventory.md").read_text(encoding="utf-8")

    assert "app.list" in inventory
    assert "app.show" in inventory
    assert "#270" in inventory
    assert "remains" in inventory
