import json
import subprocess
import sys
import venv
from pathlib import Path

from click.testing import CliRunner

from infralink.cli.main import cli


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
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert completed.stdout.count("\n") == 1
    assert payload["result"]["path"] == []


def test_installed_wheel_entrypoints_emit_json(tmp_path: Path) -> None:
    project_root = Path(__file__).resolve().parents[1]
    dist_dir = tmp_path / "dist"
    subprocess.run(
        [sys.executable, "-m", "build", "--outdir", str(dist_dir)],
        cwd=project_root,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel = next(dist_dir.glob("infralink-*.whl"))
    install_venv = tmp_path / "install-venv"
    venv.create(install_venv, with_pip=True)
    install_python = install_venv / "bin" / "python"
    subprocess.run(
        [install_python, "-m", "pip", "install", str(wheel)],
        check=True,
        capture_output=True,
        text=True,
    )

    invocations = [
        [str(install_venv / "bin" / "infralink"), "--help"],
        [str(install_python), "-m", "infralink", "--version"],
    ]
    for invocation in invocations:
        completed = subprocess.run(
            invocation,
            cwd=tmp_path,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0
        assert completed.stderr == ""
        assert completed.stdout.count("\n") == 1
        assert json.loads(completed.stdout)["ok"] is True
