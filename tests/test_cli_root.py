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
    assert missing_payload["next_actions"][0]["argv"] == ["infralink", "help", "hosts"]


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
    assert environment_payload["result"]["page"]["total"] > 0
    assert override_payload["result"]["page"]["total"] == 0


def test_explicit_json_is_preserved_by_generated_follow_up_actions() -> None:
    root = Path(__file__).resolve().parents[1]
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(root / "examples/registry.yml"),
            "--edges",
            str(root / "examples/edges.yml"),
            "services",
            "--limit",
            "1",
        ],
    )

    payload = json.loads(result.output)
    continuation = next(action for action in payload["next_actions"] if action["rel"] == "continue")
    cursor = payload["result"]["page"]["next_cursor"]
    replay = CliRunner().invoke(
        cli,
        [cursor if value == "{cursor}" else value for value in continuation["argv"][1:]],
    )

    assert replay.exit_code == 0
    assert replay.output.startswith("{")
    assert json.loads(replay.output)["command"]["resolved"]["output"] == "json"


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
        child["action"] == {"rel": "help", "command": f"infralink help {child['name']}"}
        and "\n" not in child["summary"]
        for child in children
    )
    assert "arguments" not in children[0]
    assert "options" not in children[0]
    assert all("Examples:" not in child["summary"] for child in children)
    assert result.output.count("\n") <= 120


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
        "action": {"rel": "help", "command": "infralink help live-child"},
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
