from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = PROJECT_ROOT / ".github" / "workflows"
WOODPECKER = PROJECT_ROOT / ".woodpecker.yml"


def load_woodpecker() -> dict[str, object]:
    return yaml.safe_load(WOODPECKER.read_text(encoding="utf-8"))


def test_github_actions_only_projects_released_distributions_to_package_indexes() -> None:
    assert sorted(path.name for path in WORKFLOWS.iterdir()) == ["publish-pypi.yml"]


def test_woodpecker_python_312_quality_gate_is_authoritative() -> None:
    workflow = load_woodpecker()

    assert workflow["when"] == [
        {"event": "push"},
        {"event": "pull_request"},
        {"event": "manual"},
    ]
    assert "matrix" not in workflow
    for version in ("3.12",):
        quality = workflow["steps"][f"quality-{version}"]
        assert quality["image"] == f"python:{version}-slim-bookworm"
        assert quality["depends_on"] == []
        commands = "\n".join(quality["commands"])
        for required in (
            "cp -a . /tmp/infralink-quality && cd /tmp/infralink-quality",
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
