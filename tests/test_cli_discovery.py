import json
from collections.abc import Callable

import click
import pytest
from click.testing import CliRunner

import infralink.cli.main as cli_main
from infralink.cli.actions import action
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import cli, main, run


def invoke(*args: str):
    return CliRunner().invoke(cli, list(args))


def payload_for(*args: str) -> dict:
    result = invoke(*args)
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return json.loads(result.output)


def test_root_discovers_commands_as_json() -> None:
    payload = payload_for()
    assert payload["result"]["version"] == "0.2.0"
    assert {"help", "version", "hosts", "services", "edges-list"} <= {
        item["name"] for item in payload["result"]["commands"]
    }


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


def test_nested_help_is_json() -> None:
    payload = payload_for("host", "show", "--help")
    assert payload["result"]["path"] == ["host", "show"]
    assert payload["result"]["arguments"][0]["name"] == "host_id"


def test_existing_nested_help_is_json() -> None:
    payload = payload_for("app", "show", "--help")
    assert payload["result"]["path"] == ["app", "show"]
    assert payload["result"]["arguments"][0]["name"] == "app_id"


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


def _install_test_command(
    monkeypatch: pytest.MonkeyPatch, callback: Callable[[], None]
) -> None:
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


def test_wrapper_and_click_object_have_identical_parse_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    direct = invoke("--unknown")
    assert main(["--unknown"]) == direct.exit_code == 2
    captured = capsys.readouterr()
    assert json.loads(captured.out)["error"]["code"] == "usage_error"
    assert captured.err == ""
