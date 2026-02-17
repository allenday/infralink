import json
from pathlib import Path
from click.testing import CliRunner

from infralink.cli.main import cli

EXAMPLES = Path(__file__).parent.parent / "examples"


def run_cmd(args: list[str]):
    runner = CliRunner()
    result = runner.invoke(cli, args)
    return result


def test_validate_json_ok():
    result = run_cmd([
        "--registry", str(EXAMPLES / "registry.yml"),
        "--edges", str(EXAMPLES / "edges.yml"),
        "--output", "json",
        "validate",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] in {"ok", "warn"}
    assert payload["summary"]["errors"] == 0


def test_validate_json_error_on_missing_registry():
    result = run_cmd([
        "--registry", "nope.yml",
        "--edges", str(EXAMPLES / "edges.yml"),
        "--output", "json",
        "validate",
    ])
    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["status"] == "error"


def test_hosts_json():
    result = run_cmd([
        "--registry", str(EXAMPLES / "registry.yml"),
        "--output", "json",
        "hosts",
    ])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["status"] == "ok"
    assert isinstance(payload.get("hosts"), list)
    assert len(payload["hosts"]) > 0
