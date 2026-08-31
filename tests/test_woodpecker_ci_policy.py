"""Policy tests for the secret-free Woodpecker quality steps."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".woodpecker.yml"
EXPECTED_COMMANDS = [
    "cp -a . /tmp/infralink-quality && cd /tmp/infralink-quality",
    "sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list.d/debian.sources",
    "apt-get update",
    "apt-get install --yes --no-install-recommends git",
    'python -m pip install --disable-pip-version-check -e ".[dev]"',
    "python -m ruff format --check src tests scripts",
    "python -m ruff check src tests scripts",
    "python -m mypy src scripts",
    "python -m pytest",
    "python scripts/generate_cli_schemas.py",
    "python scripts/generate_observation_schemas.py",
    "python scripts/generate_release_schemas.py",
    "python scripts/check_docs.py",
    "git diff --exit-code",
    'test -z "$(git ls-files --others --exclude-standard src/infralink/schemas)"',
    "python -m build",
    "python -m twine check dist/*",
]


def _walk(value: Any) -> Iterator[tuple[str | None, Any]]:
    if isinstance(value, dict):
        for key, child in value.items():
            yield str(key), child
            yield from _walk(child)
    elif isinstance(value, list):
        for child in value:
            yield None, child
            yield from _walk(child)


def test_quality_pipeline_contract() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    assert workflow["when"] == [
        {"event": "push"},
        {"event": "pull_request"},
        {"event": "manual"},
    ]
    assert "matrix" not in workflow
    for version in ("3.12",):
        assert workflow["steps"][f"quality-{version}"] == {
            "image": f"python:{version}-slim-bookworm",
            "depends_on": [],
            "when": [
                {
                    "path": {
                        "exclude": [
                            "README.md",
                            "docs/**",
                            "tests/test_docs_contract.py",
                            ".woodpecker.yml",
                        ]
                    }
                }
            ],
            "commands": EXPECTED_COMMANDS,
        }


def test_quality_pipeline_has_no_operational_capabilities() -> None:
    steps = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["steps"]

    forbidden_keys = {"secrets", "privileged", "volumes", "services"}
    quality_steps = [steps[f"quality-{version}"] for version in ("3.12",)]
    assert all(
        forbidden_keys.isdisjoint({key for key, _value in _walk(quality) if key is not None})
        for quality in quality_steps
    )

    commands = [
        value.lower()
        for quality in quality_steps
        for key, value in _walk(quality)
        if key is None and isinstance(value, str)
    ]
    forbidden_patterns = (
        r"\b(tag|push|upload|publish|deploy|release)\b",
        r"\b(docker|podman|oras)\s+login\b",
        r"\bcurl\b.*(?:-x|--request)\s+(post|put|patch|delete)\b",
        r"\bwget\b.*--post-(data|file)\b",
        r"\b(ssh|scp|sftp|rsync)\b",
    )
    assert not any(
        re.search(pattern, command) for pattern in forbidden_patterns for command in commands
    )
