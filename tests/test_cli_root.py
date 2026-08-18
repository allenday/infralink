import json
import shlex
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
    assert missing_payload["next_actions"][0]["command"] == "infralink help host list"


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


def test_doctor_derives_standard_sources_from_configured_registry(
    monkeypatch, tmp_path: Path
) -> None:
    """One local registry selection is enough for the standard Doctor invocation."""
    root = Path(__file__).resolve().parents[1]
    host_id = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
    checkout = tmp_path / "infra-registry"
    hosts = checkout / "hosts"
    host_root = hosts / host_id
    host_root.mkdir(parents=True)
    (host_root / "manifest.yml").write_text(
        yaml.safe_dump(
            {
                "hosts": {
                    host_id: {
                        "canonical_name": "database.example.com",
                        "status": "active",
                        "tailscale_ip": "100.64.0.10",
                        "controller_bootstrap": {
                            "registry_read_identity_secret": {
                                "project": "infra",
                                "id": "registry-reader",
                            },
                            "registry_repo_url": "ssh://git@example.invalid/infra-registry.git",
                            "registry_ref": "main",
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (hosts / "applications.yml").write_text(
        yaml.safe_dump(
            {"applications": {"database": {"members": [{"host": host_id, "services": []}]}}},
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    operations = host_root / "operations"
    operations.mkdir()
    (operations / "deployment.yml").write_text(
        yaml.safe_dump(
            {
                "schema_version": "self-deploy.desired-state.v1",
                "machine": {"uuid": host_id},
                "controller": {"image": {"repository": "example/controller", "tag": "v1"}},
                "infra_management": {"revision": "a" * 40},
                "compose": {"project_name": "services"},
                "images": {"node-exporter": {"repository": "prom/node-exporter", "tag": "v1"}},
                "services": {"protected": ["node-exporter"]},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    edges = checkout / "network/main-dev/edges/edges.yml"
    edges.parent.mkdir(parents=True)
    edges.write_text((root / "examples/edges.yml").read_text(encoding="utf-8"), encoding="utf-8")
    observation = checkout / "operations/observation"
    observation.mkdir(parents=True)
    (observation / "core-plan.json").write_text('{"dependencies": []}', encoding="utf-8")
    (observation / "adapter-bindings.yml").write_text("bindings: []\n", encoding="utf-8")
    config_home = tmp_path / "config"
    (config_home / "infralink").mkdir(parents=True)
    (config_home / "infralink/config.yml").write_text(
        yaml.safe_dump({"registry": str(checkout)}, sort_keys=False), encoding="utf-8"
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("INFRALINK_REGISTRY", raising=False)
    monkeypatch.delenv("INFRALINK_EDGES", raising=False)

    result = CliRunner().invoke(cli, ["--output", "json", "doctor", "host", host_id, "--validate"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["command"]["resolved"]["registry"] == str(checkout)
    assert payload["command"]["resolved"]["edges"] == str(edges)
    assert payload["command"]["resolved"]["observation_plan"] == str(observation / "core-plan.json")
    assert payload["command"]["resolved"]["adapter_bindings"] == str(
        observation / "adapter-bindings.yml"
    )
    applications = CliRunner().invoke(cli, ["--output", "json", "app", "list"])
    applications_payload = json.loads(applications.output)
    assert applications.exit_code == 0
    assert applications_payload["result"]["items"] == ["database"]


def test_explicit_registry_sources_override_local_config(monkeypatch, tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    config_home = tmp_path / "config"
    (config_home / "infralink").mkdir(parents=True)
    (config_home / "infralink/config.yml").write_text(
        yaml.safe_dump({"registry": str(tmp_path / "configured-checkout")}, sort_keys=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("INFRALINK_REGISTRY", str(root / "examples/registry.yml"))
    alternate = tmp_path / "alternate.yml"
    alternate.write_text("hosts: {}\n", encoding="utf-8")

    from_environment = CliRunner().invoke(cli, ["--output", "json", "host", "list"])
    with_flag = CliRunner().invoke(
        cli, ["--output", "json", "--registry", str(alternate), "host", "list"]
    )

    environment_payload = json.loads(from_environment.output)
    flag_payload = json.loads(with_flag.output)
    assert from_environment.exit_code == with_flag.exit_code == 0
    assert environment_payload["command"]["resolved"]["registry"] == str(
        root / "examples/registry.yml"
    )
    assert flag_payload["command"]["resolved"]["registry"] == str(alternate)


def test_malformed_local_config_does_not_block_source_independent_commands(
    monkeypatch, tmp_path: Path
) -> None:
    config_home = tmp_path / "config"
    (config_home / "infralink").mkdir(parents=True)
    (config_home / "infralink/config.yml").write_text("- not-a-mapping\n", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("INFRALINK_REGISTRY", raising=False)

    for command in ([], ["version"], ["help"]):
        result = CliRunner().invoke(cli, command)
        assert result.exit_code == 0


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
    assert payload["next_actions"][0]["command"] == "infralink help validate"

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
    assert (
        json.loads(json_result.output)["next_actions"][0]["command"]
        == "infralink --output json help validate"
    )


def test_bare_group_usage_preserves_explicit_json_output() -> None:
    result = CliRunner().invoke(cli, ["--output", "json", "host"])

    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert all(action["command"].startswith("infralink ") for action in payload["next_actions"])


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
        cli, [host_id if value == "{id}" else value for value in shlex.split(show["command"])[1:]]
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
    assert {child["name"] for child in help_payload["result"]["children"]} == {
        "apply",
        "bootstrap",
        "create",
        "list",
        "logs",
        "show",
        "status",
        "verifier",
    }
    assert yaml.safe_load(listed.output)["result"] == yaml.safe_load(compatibility.output)["result"]

    bare_payload = yaml.safe_load(bare.output)
    assert bare.exit_code == 2
    assert {shlex.split(action["command"])[-1] for action in bare_payload["next_actions"]} == {
        "apply",
        "bootstrap",
        "create",
        "list",
        "logs",
        "show",
        "status",
        "verifier",
    }
    assert all(
        action["command"].startswith("infralink help host")
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
        assert all(item["command"].startswith("infralink ") for item in actions)
        show = next(item for item in actions if item["rel"] == "show")
        assert "argv" not in show
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
