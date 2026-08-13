import json
from collections.abc import Callable
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

import infralink.cli.main as cli_main
from infralink.cli.actions import action
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import cli, main, run

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def invoke(*args: str):
    return CliRunner().invoke(cli, ["--output", "json", *args])


def payload_for(*args: str) -> dict:
    result = invoke(*args)
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return json.loads(result.output)


def assert_schema(payload: dict, name: str) -> None:
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1" / f"{name}.json").read_text())
    Draft202012Validator(schema).validate(payload)


def test_root_discovers_canonical_commands_as_json() -> None:
    payload = payload_for()
    assert payload["result"]["version"] == "0.5.1"
    assert {"help", "version", "host", "service", "edge"} <= {
        item["name"] for item in payload["result"]["commands"]
    }
    assert_schema(payload, "root")


def test_every_advertised_command_is_a_real_click_command() -> None:
    payload = payload_for()
    for descriptor in payload["result"]["commands"]:
        command = cli_main._load_command(descriptor["name"])
        assert isinstance(command, click.Command)
        assert command is cli_main.help_command or command.callback is not cli_main._emit_help


def test_help_is_json() -> None:
    payload = payload_for("help", "resolve")
    assert payload["result"]["path"] == ["resolve"]
    assert payload["result"]["arguments"][0]["name"] == "edge_id"
    assert payload["result"]["options"]
    assert payload["result"]["children"] == []


def test_click_help_aliases_are_json() -> None:
    root_help = payload_for("--help")
    assert root_help["result"]["path"] == []
    assert root_help["command"]["raw"] == "infralink --output json --help"
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
        (("resolve", "--prefer-ip", "public", "--help"), ["resolve"]),
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
    assert option_names == {
        "user",
        "database",
        "prefer_ip",
    }
    assert {"password", "password_env"}.isdisjoint(option_names)


def test_host_detail_help_is_live_and_command_is_registered() -> None:
    payload = payload_for("help", "host", "show")
    assert "show one host" in payload["result"]["description"].lower()
    assert isinstance(cli_main._load_command("host"), click.Group)


def test_version_is_json() -> None:
    payload = payload_for("version")
    assert payload["result"] == {
        "version": "0.5.1",
        "cli_schema_version": "infralink.cli/v1",
    }


def test_click_version_alias_is_json() -> None:
    payload = payload_for("--version")
    assert payload["result"]["version"] == "0.5.1"
    assert payload["command"]["raw"] == "infralink --output json --version"


@pytest.mark.parametrize(
    ("args", "expected_code"),
    [
        (("not-a-command",), "usage_error"),
        (("--unknown",), "usage_error"),
        (("resolve",), "usage_error"),
        (("resolve", "--prefer-ip", "invalid", "edge-1"), "usage_error"),
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
        "--prefer-ip",
        "public",
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
    assert yaml.safe_load(captured.out)["error"]["code"] == "provider_unavailable"
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
    assert yaml.safe_load(captured.out)["error"]["code"] == "internal_error"
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


def test_system_exit_integer_becomes_one_internal_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_test_command(monkeypatch, lambda: raise_system_exit(5))

    direct = invoke("explode")
    assert direct.exit_code == 70
    assert direct.stderr == ""
    assert direct.output.count("\n") == 1
    assert json.loads(direct.output)["error"]["code"] == "internal_error"

    assert main(["explode"]) == 70
    captured = capsys.readouterr()
    assert captured.err == ""
    assert yaml.safe_load(captured.out)["error"]["code"] == "internal_error"


@pytest.mark.parametrize("failure_kind", ["cli_failure", "runtime_error"])
def test_emitted_envelope_is_not_duplicated_by_later_failure(
    failure_kind: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def emit_then_fail() -> None:
        cli_main._emit({"first": True})
        if failure_kind == "cli_failure":
            raise CliFailure(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Provider unavailable",
                exit_code=4,
                fix="Retry later",
            )
        raise RuntimeError("canary-secret")

    _install_test_command(monkeypatch, emit_then_fail)
    expected_exit = 4 if failure_kind == "cli_failure" else 70

    def assert_expected_payload(output: str) -> None:
        payload = yaml.safe_load(output)
        if failure_kind == "cli_failure":
            assert payload == {"first": True}
        else:
            assert payload["error"]["code"] == "internal_error"
            assert "canary-secret" not in output

    direct = invoke("explode")
    assert direct.exit_code == expected_exit
    assert direct.stderr == ""
    assert direct.output.count("\n") == 1
    assert_expected_payload(direct.output)

    assert main(["explode"]) == expected_exit
    captured = capsys.readouterr()
    assert captured.err == ""
    assert_expected_payload(captured.out)

    with pytest.raises(SystemExit) as caught:
        run(["explode"])
    assert caught.value.code == expected_exit
    captured = capsys.readouterr()
    assert captured.err == ""
    assert_expected_payload(captured.out)

    following = invoke("--version")
    assert following.exit_code == 0
    assert json.loads(following.output)["result"]["version"] == "0.5.1"


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


def test_advertised_typed_commands_produce_their_schemas() -> None:
    invocations = {
        "help": (("help",), "help"),
        "version": (("version",), "version"),
        "services": (
            ("--registry", str(EXAMPLES / "registry.yml"), "services"),
            "services",
        ),
        "resolve": (
            (
                "--registry",
                str(EXAMPLES / "registry.yml"),
                "--edges",
                str(EXAMPLES / "edges.yml"),
                "resolve",
                "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
            ),
            "resolve",
        ),
    }
    for args, schema_name in invocations.values():
        result = invoke(*args)
        assert result.exit_code == 0, result.output
        assert_schema(json.loads(result.output), schema_name)


def test_live_command_discovery_is_locked_to_checked_in_schema_coverage() -> None:
    schema_coverage = {
        "help": {"help"},
        "version": {"version"},
        "analyze": {"analyze"},
        "check": {"check"},
        "doctor": {"doctor"},
        "diagram": {"diagram"},
        "docs": {"docs"},
        "resolve": {"resolve"},
        "secrets": {"secrets-audit", "secrets-inspect"},
        "release": {
            "release-inspect",
            "release-validate-candidate",
            "release-render-publisher-request",
            "release-inspect-attestation",
        },
        "validate": {"validate", "observation-validate"},
        "capabilities": {"capabilities"},
        "explain": {"explain"},
        "project": {
            "project-observation",
            "project-secrets",
            "project-view",
            "project-readiness",
        },
        "app": {"app-list", "app-show"},
        "info": {"info"},
        "host": {
            "hosts",
            "host-show",
            "host-bootstrap",
            "host-verifier",
            "host-apply",
            "host-status",
            "host-logs",
        },
        "operation": {"operation-status"},
        "edge": {"edges-list", "edge-show"},
        "service": {"services", "service-show"},
    }
    live_commands = {item["name"] for item in payload_for()["result"]["commands"]}
    schema_names = {path.stem for path in (ROOT / "src/infralink/schemas/cli/v1").glob("*.json")}

    assert live_commands == set(schema_coverage)
    assert set().union(*schema_coverage.values()) | {"root"} == schema_names


def test_successful_info_and_resolve_match_checked_in_schemas() -> None:
    source_args = (
        "--registry",
        str(EXAMPLES / "registry.yml"),
        "--edges",
        str(EXAMPLES / "edges.yml"),
    )
    info_result = invoke(*source_args, "info")
    resolve_result = invoke(
        *source_args,
        "resolve",
        "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
    )

    assert info_result.exit_code == 0
    assert resolve_result.exit_code == 0
    assert_schema(json.loads(info_result.output), "info")
    assert_schema(json.loads(resolve_result.output), "resolve")


def test_services_include_role_explicit_and_edge_target_identities() -> None:
    payload = payload_for(
        "--registry",
        str(EXAMPLES / "registry.yml"),
        "--edges",
        str(EXAMPLES / "edges.yml"),
        "service",
        "list",
    )
    service_ids = set(payload["result"]["items"])
    assert {"postgresql", "redis", "nginx", "node-exporter"} <= service_ids
    assert "page" not in payload["result"]
    show = next(action for action in payload["next_actions"] if action["rel"] == "show")
    assert show["bindings"]["id"]["source"] == "result.items[]"


def test_services_list_all_declared_scalar_ids(tmp_path: Path) -> None:
    services = "\n".join(
        f"      service-{index}:\n        port: {10000 + index}\n" for index in range(101)
    )
    registry = tmp_path / "registry.yml"
    registry.write_text(
        "hosts:\n"
        "  11111111-1111-1111-1111-111111111111:\n"
        "    canonical_name: complete-host\n"
        "    status: active\n"
        "    services:\n"
        f"{services}"
    )
    payload = payload_for("--registry", str(registry), "service", "list")
    generated = [item for item in payload["result"]["items"] if item.startswith("service-")]
    assert len(generated) == 101
    assert "page" not in payload["result"]
    assert payload["meta"]["truncated"] is False


@pytest.mark.parametrize(
    "command_args",
    [
        ("check",),
        ("diagram", "--output", "generated"),
        ("docs", "--output", "generated"),
        ("app", "list"),
    ],
)
def test_commands_delegate_missing_registry_to_json_boundary(
    command_args: tuple[str, ...],
) -> None:
    result = invoke("--registry", "missing.yml", *command_args)
    payload = json.loads(result.output)
    assert result.exit_code == 3
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert payload["error"]["code"] == "input_load_failed"
    assert payload["error"]["details"] == {
        "source": "registry",
        "path": "missing.yml",
    }


def test_wrapper_and_click_object_have_identical_parse_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    direct = invoke("--unknown")
    assert main(["--unknown"]) == direct.exit_code == 2
    captured = capsys.readouterr()
    assert yaml.safe_load(captured.out)["error"]["code"] == "usage_error"
    assert captured.err == ""
