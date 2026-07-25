import json
from collections.abc import Callable
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from jsonschema import Draft202012Validator

import infralink.cli.main as cli_main
from infralink.cli.actions import action
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import cli, main, run

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def invoke(*args: str):
    return CliRunner().invoke(cli, list(args))


def payload_for(*args: str) -> dict:
    result = invoke(*args)
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return json.loads(result.output)


def assert_schema(payload: dict, name: str) -> None:
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1" / f"{name}.json").read_text())
    Draft202012Validator(schema).validate(payload)


def test_root_discovers_commands_as_json() -> None:
    payload = payload_for()
    assert payload["result"]["version"] == "0.2.0"
    assert {"help", "version", "services"} == {
        item["name"] for item in payload["result"]["commands"]
    }
    assert_schema(payload, "root")


def test_help_is_json() -> None:
    payload = payload_for("help", "resolve")
    assert payload["result"]["path"] == ["resolve"]
    assert payload["result"]["arguments"][0]["name"] == "edge_id"
    assert payload["result"]["options"]
    assert payload["result"]["examples"]


def test_click_help_aliases_are_json() -> None:
    root_help = payload_for("--help")
    assert root_help["result"]["path"] == []
    assert root_help["command"]["raw"] == "infralink --help"
    assert payload_for("resolve", "--help")["result"]["path"] == ["resolve"]


def test_existing_nested_help_is_json() -> None:
    payload = payload_for("app", "show", "--help")
    assert payload["result"]["path"] == ["app", "show"]
    assert payload["result"]["arguments"][0]["name"] == "app_id"


@pytest.mark.parametrize(
    ("args", "path"),
    [
        (("-v", "--help"), []),
        (("--registry=registry.yml", "--help"), []),
        (("--registry", "registry.yml", "--edges", "edges.yml", "--help"), []),
        (("resolve", "edge-1", "--help"), ["resolve"]),
        (("resolve", "--format", "json", "--help"), ["resolve"]),
        (("app", "show", "core", "--help"), ["app", "show"]),
        (("host", "show", "host-1", "--help"), ["host", "show"]),
    ],
)
def test_help_alias_ignores_flags_options_and_positionals(
    args: tuple[str, ...], path: list[str]
) -> None:
    payload = payload_for(*args)
    assert payload["result"]["path"] == path
    assert_schema(payload, "help")


def test_help_describes_every_live_resolve_option() -> None:
    payload = payload_for("help", "resolve")
    option_names = {option["name"] for option in payload["result"]["options"]}
    assert {
        "format",
        "user",
        "password",
        "password_env",
        "database",
        "prefer_ip",
    } <= option_names


def test_future_help_is_marked_unavailable_without_registering_command() -> None:
    payload = payload_for("help", "host", "show")
    assert "unavailable" in payload["result"]["description"].lower()
    absent = invoke("host", "show", "host-1")
    assert absent.exit_code == 2
    assert json.loads(absent.output)["error"]["code"] == "usage_error"


def test_version_is_json() -> None:
    payload = payload_for("version")
    assert payload["result"] == {
        "version": "0.2.0",
        "cli_schema_version": "infralink.cli/v1",
    }


def test_click_version_alias_is_json() -> None:
    payload = payload_for("--version")
    assert payload["result"]["version"] == "0.2.0"
    assert payload["command"]["raw"] == "infralink --version"


@pytest.mark.parametrize(
    ("args", "expected_code"),
    [
        (("not-a-command",), "usage_error"),
        (("--unknown",), "usage_error"),
        (("resolve",), "usage_error"),
        (("resolve", "--format", "invalid", "edge-1"), "usage_error"),
    ],
)
def test_malformed_invocations_are_json_usage_errors(
    args: tuple[str, ...], expected_code: str
) -> None:
    result = invoke(*args)
    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert payload["error"]["code"] == expected_code


def test_missing_registry_is_json_input_error() -> None:
    result = invoke("--registry", "missing.yml", "info")
    payload = json.loads(result.output)
    assert result.exit_code == 3
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert payload["error"]["code"] == "input_load_failed"


@pytest.mark.parametrize(
    "credential_arg",
    [
        "--password=canary-secret",
        "-pcanary-secret",
        "--token=canary-secret",
    ],
)
def test_usage_errors_never_capture_credential_tokens(credential_arg: str) -> None:
    result = invoke(
        "--registry",
        "registry.yml",
        "resolve",
        "edge-1",
        credential_arg,
        "--unknown",
    )
    serialized = result.output
    payload = json.loads(serialized)
    assert result.exit_code == 2
    assert "canary-secret" not in serialized
    assert payload["command"]["parsed"]["path"] == ["resolve"]
    assert payload["command"]["parsed"]["args"] == {"edge_id": "edge-1"}
    assert "canary-secret" not in json.dumps(payload["command"]["parsed"]["flags"])
    assert "canary-secret" not in json.dumps(payload["command"]["resolved"])


def test_context_reports_safe_bound_values() -> None:
    payload = payload_for(
        "--registry=registry.yml",
        "--edges",
        "edges.yml",
        "-v",
        "resolve",
        "edge-1",
        "--format",
        "json",
        "--help",
    )
    assert payload["command"]["parsed"]["path"] == ["resolve"]
    assert payload["command"]["parsed"]["args"] == {"edge_id": "edge-1"}
    assert {
        "registry": "registry.yml",
        "edges": "edges.yml",
        "output": "json",
        "verbose": True,
    }.items() <= payload["command"]["resolved"].items()


@pytest.mark.parametrize(
    "content",
    [
        "hosts:\n  canary-secret: [not-a-host]\n",
        "hosts: [canary-secret\n",
    ],
    ids=["pydantic", "yaml"],
)
def test_malformed_registry_is_safe_input_failure(tmp_path: Path, content: str) -> None:
    registry = tmp_path / "registry.yml"
    registry.write_text(content)
    result = invoke("--registry", str(registry), "services")
    payload = json.loads(result.output)
    assert result.exit_code == 3
    assert payload["error"]["code"] == "input_load_failed"
    assert payload["error"]["details"] == {
        "source": "registry",
        "path": str(registry),
    }
    serialized = json.dumps(payload)
    assert "canary-secret" not in serialized
    assert "input_value" not in serialized


def test_malformed_edges_are_safe_input_failure(tmp_path: Path) -> None:
    edges = tmp_path / "edges.yml"
    edges.write_text("edges:\n  - canary-secret\n")
    result = invoke(
        "--registry",
        str(EXAMPLES / "registry.yml"),
        "--edges",
        str(edges),
        "info",
    )
    payload = json.loads(result.output)
    assert result.exit_code == 3
    assert payload["error"]["code"] == "input_load_failed"
    assert payload["error"]["details"] == {"source": "edges", "path": str(edges)}
    assert "canary-secret" not in json.dumps(payload)


def _install_test_command(monkeypatch: pytest.MonkeyPatch, callback: Callable[[], None]) -> None:
    command = click.Command("explode", callback=callback)
    original = cli_main._load_command
    monkeypatch.setitem(
        cli_main.COMMAND_METADATA,
        "explode",
        {"description": "Test boundary.", "usage": "infralink explode"},
    )
    monkeypatch.setattr(
        cli_main,
        "_load_command",
        lambda name: command if name == "explode" else original(name),
    )


def test_cli_failure_crosses_direct_and_public_boundaries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    failure = CliFailure(
        code=ErrorCode.PROVIDER_UNAVAILABLE,
        message="Provider unavailable",
        exit_code=4,
        fix="Retry later",
        next_actions=[action("retry", ["infralink", "explode"], "Retry")],
    )
    _install_test_command(monkeypatch, lambda: (_ for _ in ()).throw(failure))

    direct = invoke("explode")
    assert direct.exit_code == 4
    assert json.loads(direct.output)["error"]["code"] == "provider_unavailable"
    assert direct.stderr == ""

    assert main(["explode"]) == 4
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "provider_unavailable"
    assert captured.err == ""


def test_unexpected_exception_is_redacted_at_both_boundaries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_test_command(
        monkeypatch,
        lambda: (_ for _ in ()).throw(RuntimeError("canary-secret")),
    )

    direct = invoke("explode")
    assert direct.exit_code == 70
    assert "canary-secret" not in direct.output
    assert json.loads(direct.output)["error"]["code"] == "internal_error"
    assert direct.stderr == ""

    assert main(["explode"]) == 70
    captured = capsys.readouterr()
    assert "canary-secret" not in captured.out
    assert json.loads(captured.out)["error"]["code"] == "internal_error"
    assert captured.err == ""


def test_run_raises_system_exit_with_main_status() -> None:
    with pytest.raises(SystemExit) as caught:
        run(["--unknown"])
    assert caught.value.code == 2


def test_system_exit_text_is_suppressed_at_all_boundaries(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_test_command(monkeypatch, lambda: raise_system_exit("canary-secret"))

    direct = invoke("explode")
    assert direct.exit_code == 70
    assert "canary-secret" not in direct.output
    assert json.loads(direct.output)["error"]["code"] == "internal_error"

    assert main(["explode"]) == 70
    captured = capsys.readouterr()
    assert "canary-secret" not in captured.out + captured.err

    with pytest.raises(SystemExit) as caught:
        run(["explode"])
    assert caught.value.code == 70
    captured = capsys.readouterr()
    assert "canary-secret" not in captured.out + captured.err


def raise_system_exit(code: object) -> None:
    raise SystemExit(code)


def test_resolve_absent_is_one_json_entity_failure() -> None:
    result = invoke(
        "--registry",
        str(EXAMPLES / "registry.yml"),
        "--edges",
        str(EXAMPLES / "edges.yml"),
        "resolve",
        "absent",
    )
    payload = json.loads(result.output)
    assert result.exit_code == 3
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert payload["error"]["code"] == "entity_not_found"


def test_every_advertised_command_produces_its_schema() -> None:
    root = payload_for()
    invocations = {
        "help": (("help",), "help"),
        "version": (("version",), "version"),
        "services": (
            ("--registry", str(EXAMPLES / "registry.yml"), "services"),
            "services",
        ),
    }
    for descriptor in root["result"]["commands"]:
        args, schema_name = invocations[descriptor["name"]]
        result = invoke(*args)
        assert result.exit_code == 0, (descriptor["name"], result.output)
        assert_schema(json.loads(result.output), schema_name)


def test_wrapper_and_click_object_have_identical_parse_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    direct = invoke("--unknown")
    assert main(["--unknown"]) == direct.exit_code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "usage_error"
    assert captured.err == ""
