from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
WOODPECKER = PROJECT_ROOT / ".woodpecker.yml"


def load_woodpecker() -> dict[str, object]:
    return yaml.safe_load(WOODPECKER.read_text(encoding="utf-8"))


def test_github_actions_workflows_are_absent() -> None:
    assert not WORKFLOWS.exists() or list(WORKFLOWS.iterdir()) == []


def test_woodpecker_quality_matrix_is_authoritative() -> None:
    workflow = load_woodpecker()

    assert workflow["when"] == [
        {"event": "push"},
        {"event": "pull_request"},
        {"event": "manual"},
    ]
    assert workflow["matrix"] == {"PYTHON_VERSION": ["3.10", "3.11", "3.12"]}
    quality = workflow["steps"]["quality"]
    assert quality["image"] == "python:${PYTHON_VERSION}-slim-bookworm"
    commands = "\n".join(quality["commands"])
    for required in (
        'python -m pip install --disable-pip-version-check -e ".[dev]"',
        "python -m ruff format --check src tests scripts",
        "python -m ruff check src tests scripts",
        "python -m mypy src scripts",
        "python -m pytest",
        "python scripts/generate_cli_schemas.py",
        "git diff --exit-code",
        "python -m build",
    ):
        assert required in commands
