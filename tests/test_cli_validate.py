import json

from click.testing import CliRunner

from infralink.cli.main import cli


def test_validate_returns_json_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["--registry", "missing.yml", "validate"])
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert "fix" in payload
