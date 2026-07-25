import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from infralink.cli.main import cli

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def validate_schema(payload: dict) -> None:
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/validate.json").read_text())
    Draft202012Validator(schema).validate(payload)


def test_validate_returns_json_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["--registry", "missing.yml", "validate"])
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert "fix" in payload
    assert result.exit_code == 3
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert payload["error"]["code"] == "input_load_failed"
    validate_schema(payload)


@pytest.mark.parametrize(
    "output_args",
    [(), ("--output=json",), ("-o", "json")],
)
def test_validate_option_spellings_are_schema_equivalent(
    output_args: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(
        cli,
        [
            *output_args,
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "validate",
        ],
    )
    payload = json.loads(result.output)
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    validate_schema(payload)
    assert set(payload) == {
        "schema_version",
        "ok",
        "command",
        "result",
        "next_actions",
        "meta",
    }


def test_resolution_warnings_are_structured_without_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    monkeypatch.setattr(
        EdgeResolver,
        "validate_all",
        lambda self: ([], ["canary warning"]),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "validate",
            "--check-resolution",
        ],
    )
    payload = json.loads(result.output)
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    validate_schema(payload)
    assert payload["result"]["warnings"]["items"] == [
        {
            "code": "resolution_warning",
            "path": None,
            "message": "canary warning",
            "severity": "warning",
        }
    ]


def test_validate_does_not_silently_truncate_101_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    warnings = [f"warning-{index}" for index in range(101)]
    monkeypatch.setattr(EdgeResolver, "validate_all", lambda self: ([], warnings))
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "validate",
            "--check-resolution",
        ],
    )
    payload = json.loads(result.output)
    warning_page = payload["result"]["warnings"]
    assert len(warning_page["items"]) == 101
    assert warning_page["page"]["returned"] == warning_page["page"]["total"] == 101
    assert warning_page["page"]["next_cursor"] is None
    assert payload["meta"]["truncated"] is False
