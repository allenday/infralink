import json
import subprocess
import sys
from pathlib import Path

from click.testing import CliRunner

from infralink.cli.main import cli
from tests.cli_helpers import source_checkout_env


def test_root_command_returns_json_tree():
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"]["raw"] == "infralink"
    assert "commands" in payload["result"]


def test_module_help_is_one_json_document() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "infralink", "--help"],
        check=False,
        capture_output=True,
        text=True,
        env=source_checkout_env(),
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    assert payload["result"]["path"] == []


def test_package_declares_both_cli_entrypoints() -> None:
    project_root = Path(__file__).resolve().parents[1]
    pyproject = (project_root / "pyproject.toml").read_text(encoding="utf-8")
    module_entrypoint = project_root / "src" / "infralink" / "__main__.py"

    assert 'infralink = "infralink.cli.main:run"' in pyproject
    assert "from infralink.cli.main import run" in module_entrypoint.read_text(encoding="utf-8")
