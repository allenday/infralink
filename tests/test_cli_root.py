import json
import subprocess
import sys
from pathlib import Path

import yaml
from click.testing import CliRunner

from infralink.cli.main import cli
from tests.cli_helpers import source_checkout_env


def test_root_command_defaults_to_yaml_and_json_remains_explicit() -> None:
    runner = CliRunner()
    yaml_result = runner.invoke(cli, [])
    json_result = runner.invoke(cli, ["--output", "json"])

    assert yaml_result.exit_code == json_result.exit_code == 0
    assert yaml_result.output.startswith("schema_version:")
    yaml_payload = yaml.safe_load(yaml_result.output)
    json_payload = json.loads(json_result.output)
    assert yaml_payload["result"] == json_payload["result"]
    assert yaml_payload["ok"] is True
    assert yaml_payload["command"]["raw"] == "infralink"
    assert yaml_payload["command"]["resolved"]["output"] == "yaml"
    assert json_payload["command"]["resolved"]["output"] == "json"
    assert "commands" in yaml_payload["result"]


def test_help_and_topology_commands_default_to_yaml_and_keep_json_opt_in() -> None:
    runner = CliRunner()
    yaml_help = runner.invoke(cli, ["help", "resolve"])
    json_help = runner.invoke(cli, ["--output", "json", "help", "resolve"])
    yaml_version = runner.invoke(cli, ["version"])
    json_version = runner.invoke(cli, ["--output", "json", "version"])

    for yaml_result, json_result in (
        (yaml_help, json_help),
        (yaml_version, json_version),
    ):
        assert yaml_result.exit_code == json_result.exit_code == 0
        assert yaml_result.output.startswith("schema_version:")
        yaml_payload = yaml.safe_load(yaml_result.output)
        json_payload = json.loads(json_result.output)
        assert yaml_payload["result"] == json_payload["result"]
        assert yaml_payload["command"]["resolved"]["output"] == "yaml"
        assert json_payload["command"]["resolved"]["output"] == "json"


def test_topology_commands_require_explicit_or_environment_sources(
    monkeypatch,
) -> None:
    monkeypatch.delenv("INFRALINK_REGISTRY", raising=False)
    monkeypatch.delenv("INFRALINK_EDGES", raising=False)

    missing = CliRunner().invoke(cli, ["hosts"])

    assert missing.exit_code == 2
    missing_payload = yaml.safe_load(missing.output)
    assert missing_payload["error"]["code"] == "configuration_required"
    assert missing_payload["error"]["details"] == {"source": "registry"}
    assert missing_payload["next_actions"][0]["argv"] == [
        "infralink",
        "help",
        "host",
        "list",
    ]


def test_environment_sources_are_used_and_flags_override_them(monkeypatch, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    registry = root / "examples/registry.yml"
    edges = root / "examples/edges.yml"
    alternate = tmp_path / "alternate-registry.yml"
    alternate.write_text("hosts: {}\n", encoding="utf-8")
    monkeypatch.setenv("INFRALINK_REGISTRY", str(registry))
    monkeypatch.setenv("INFRALINK_EDGES", str(edges))

    from_environment = CliRunner().invoke(cli, ["hosts"])
    with_flag = CliRunner().invoke(cli, ["--registry", str(alternate), "hosts"])

    environment_payload = yaml.safe_load(from_environment.output)
    override_payload = yaml.safe_load(with_flag.output)
    assert from_environment.exit_code == with_flag.exit_code == 0
    assert environment_payload["command"]["resolved"]["registry"] == str(registry)
    assert override_payload["command"]["resolved"]["registry"] == str(alternate)
    assert environment_payload["result"]["items"]
    assert override_payload["result"]["items"] == []


def test_explicit_invalid_edges_path_is_an_input_failure(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    missing_edges = tmp_path / "missing-edges.yml"

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(root / "examples/registry.yml"),
            "--edges",
            str(missing_edges),
            "edge",
            "list",
        ],
    )

    payload = yaml.safe_load(result.output)
    assert result.exit_code == 3
    assert payload["error"] == {
        "code": "input_load_failed",
        "message": "Edges could not be loaded",
        "details": {"source": "edges", "path": str(missing_edges)},
    }
    assert payload["next_actions"][0]["argv"] == ["infralink", "help", "validate"]

    json_result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(root / "examples/registry.yml"),
            "--edges",
            str(missing_edges),
            "edge",
            "list",
        ],
    )
    assert json.loads(json_result.output)["next_actions"][0]["argv"] == [
        "infralink",
        "--output",
        "json",
        "help",
        "validate",
    ]


def test_bare_group_usage_preserves_explicit_json_output() -> None:
    result = CliRunner().invoke(cli, ["--output", "json", "host"])

    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert all(
        action["argv"][:3] == ["infralink", "--output", "json"]
        for action in payload["next_actions"]
    )


def test_explicit_json_is_preserved_by_generated_show_action(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("INFRALINK_REGISTRY", str(root / "examples/registry.yml"))
    monkeypatch.setenv("INFRALINK_EDGES", str(root / "examples/edges.yml"))
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "host",
            "list",
        ],
    )

    payload = json.loads(result.output)
    show = next(action for action in payload["next_actions"] if action["rel"] == "show")
    host_id = payload["result"]["items"][0]
    replay = CliRunner().invoke(
        cli,
        [host_id if value == "{id}" else value for value in show["argv"][1:]],
    )

    assert replay.exit_code == 0
    assert replay.output.startswith("{")
    assert json.loads(replay.output)["command"]["resolved"]["output"] == "json"


def test_host_group_lists_its_real_children_and_host_list_matches_compatibility_alias(
    monkeypatch,
) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("INFRALINK_REGISTRY", str(root / "examples/registry.yml"))
    monkeypatch.setenv("INFRALINK_EDGES", str(root / "examples/edges.yml"))
    runner = CliRunner()

    help_result = runner.invoke(cli, ["help", "host"])
    listed = runner.invoke(cli, ["host", "list"])
    compatibility = runner.invoke(cli, ["hosts"])
    bare = runner.invoke(cli, ["host"])

    help_payload = yaml.safe_load(help_result.output)
    assert help_result.exit_code == listed.exit_code == compatibility.exit_code == 0
    assert {child["name"] for child in help_payload["result"]["children"]} == {"list", "show"}
    assert yaml.safe_load(listed.output)["result"] == yaml.safe_load(compatibility.output)["result"]

    bare_payload = yaml.safe_load(bare.output)
    assert bare.exit_code == 2
    assert {action["argv"][-1] for action in bare_payload["next_actions"]} == {"list", "show"}
    assert all(
        action["argv"][:3] == ["infralink", "help", "host"]
        for action in bare_payload["next_actions"]
    )


def test_root_help_is_a_compact_generated_command_index() -> None:
    result = CliRunner().invoke(cli, ["help"])

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    children = payload["result"]["children"]
    assert {child["name"] for child in children} == {
        command["name"]
        for command in yaml.safe_load(CliRunner().invoke(cli, []).output)["result"]["commands"]
    }
    assert payload["next_actions"] == []
    assert all(
        child["action"]
        == {
            "rel": "help",
            "argv": ["infralink", "help", child["name"]],
            "command": f"infralink help {child['name']}",
        }
        and "\n" not in child["summary"]
        for child in children
    )
    assert "arguments" not in children[0]
    assert "options" not in children[0]
    assert all("Examples:" not in child["summary"] for child in children)
    assert result.output.count("\n") <= 220
    assert "action: {" not in result.output
    assert "{rel:" not in result.output


def test_all_list_commands_have_uniform_executable_prefixed_actions(monkeypatch) -> None:
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("INFRALINK_REGISTRY", str(root / "examples/registry.yml"))
    monkeypatch.setenv("INFRALINK_EDGES", str(root / "examples/edges.yml"))
    runner = CliRunner()

    for command, resource in (
        (["host", "list"], "host"),
        (["service", "list"], "service"),
        (["edge", "list"], "edge"),
        (["app", "list"], "app"),
        (["hosts"], "host"),
        (["services"], "service"),
        (["edges-list"], "edge"),
    ):
        response = runner.invoke(cli, command)
        assert response.exit_code == 0
        assert "action: {" not in response.output
        payload = yaml.safe_load(response.output)
        actions = payload["next_actions"]
        assert all(item["argv"][0] == "infralink" for item in actions)
        show = next(item for item in actions if item["rel"] == "show")
        assert show["argv"] == ["infralink", resource, "show", "{id}"]
        assert show["command"] == f"infralink {resource} show '{{id}}'"
        assert "continue" not in {item["rel"] for item in actions}


def test_parent_help_includes_a_live_registered_child_without_help_metadata(
    monkeypatch,
) -> None:
    import click

    import infralink.cli.main as cli_main

    command = click.Command(
        "live-child",
        params=[click.Option(["--enabled"], is_flag=True)],
        help="Registered at runtime.",
    )
    original = cli_main._load_command
    monkeypatch.setitem(
        cli_main.COMMAND_METADATA,
        "live-child",
        {"description": "Registered at runtime.", "usage": "infralink live-child"},
    )
    monkeypatch.setattr(
        cli_main,
        "_load_command",
        lambda name: command if name == "live-child" else original(name),
    )

    payload = yaml.safe_load(CliRunner().invoke(cli, ["help"]).output)
    child = next(item for item in payload["result"]["children"] if item["name"] == "live-child")

    assert child == {
        "name": "live-child",
        "summary": "Registered at runtime.",
        "action": {
            "rel": "help",
            "argv": ["infralink", "help", "live-child"],
            "command": "infralink help live-child",
        },
    }


def test_module_help_defaults_to_one_yaml_document() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "infralink", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=source_checkout_env(),
    )
    payload = yaml.safe_load(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.startswith("schema_version:")
    assert payload["result"]["path"] == []


def test_package_declares_both_cli_entrypoints() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    module_entrypoint = project_root / "src" / "infralink" / "__main__.py"

    assert 'infralink = "infralink.cli.main:run"' in pyproject
    assert "from infralink.cli.main import run" in module_entrypoint.read_text(encoding="utf-8")
