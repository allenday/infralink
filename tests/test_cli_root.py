import json

from click.testing import CliRunner

from infralink.cli.main import cli


def test_root_command_returns_json_tree():
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "infralink"
    assert "commands" in payload["result"]
