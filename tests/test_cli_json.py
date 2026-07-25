import json
from pathlib import Path

from click.testing import CliRunner

from infralink.cli.main import cli

EXAMPLES = Path(__file__).parent.parent / "examples"
EXAMPLE_EDGE_ID = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"


def run_cmd(args: list[str]):
    runner = CliRunner()
    result = runner.invoke(cli, args)
    return result


def test_validate_json_ok():
    result = run_cmd(
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "--output",
            "json",
            "validate",
        ]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["result"]["summary"]["error_count"] == 0


def test_validate_json_error_on_missing_registry():
    result = run_cmd(
        [
            "--registry",
            "nope.yml",
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "--output",
            "json",
            "validate",
        ]
    )
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "input_load_failed"


def test_hosts_json():
    result = run_cmd(
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--output",
            "json",
            "hosts",
        ]
    )
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert isinstance(payload["result"]["items"], list)
    assert len(payload["result"]["items"]) > 0


def test_resolve_rejects_password_without_leaking_it() -> None:
    canary = "resolve-canary-secret"
    result = run_cmd(
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "resolve",
            EXAMPLE_EDGE_ID,
            "--format",
            "url",
            f"--password={canary}",
        ]
    )
    assert result.exit_code == 2
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert canary not in result.output
    assert json.loads(result.output)["error"]["code"] == "usage_error"


def test_resolve_rejects_password_env_without_reading_it(
    monkeypatch,
) -> None:
    canary = "resolve-env-canary-secret"
    monkeypatch.setenv("INFRALINK_TEST_PASSWORD", canary)
    result = run_cmd(
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "resolve",
            EXAMPLE_EDGE_ID,
            "--format",
            "url",
            "--password-env",
            "INFRALINK_TEST_PASSWORD",
        ]
    )
    assert result.exit_code == 2
    assert canary not in result.output
    assert json.loads(result.output)["error"]["code"] == "usage_error"


def test_resolve_url_preserves_safe_user_and_database() -> None:
    result = run_cmd(
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "resolve",
            EXAMPLE_EDGE_ID,
            "--format",
            "url",
            "--user",
            "reporter",
            "--database",
            "analytics",
        ]
    )
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert "reporter@" in payload["result"]["url"]
    assert payload["result"]["url"].endswith("/analytics")
