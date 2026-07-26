"""Policy tests for the secret-free Woodpecker quality pipeline."""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".woodpecker.yml"
EXPECTED_COMMANDS = [
    "apt-get update",
    "apt-get install --yes --no-install-recommends git",
    'python -m pip install --disable-pip-version-check -e ".[dev]"',
    "python -m ruff format --check src tests scripts",
    "python -m ruff check src tests scripts",
    "python -m mypy src scripts",
    "python -m pytest",
    "python scripts/generate_cli_schemas.py",
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

    # One matrix-interpolated image field cannot select distinct patch or digest
    # pins without replacing this single-step contract with conditional steps.
    assert workflow == {
        "when": [
            {"event": "push"},
            {"event": "pull_request"},
            {"event": "manual"},
        ],
        "matrix": {"PYTHON_VERSION": ["3.10", "3.11", "3.12"]},
        "steps": {
            "quality": {
                "image": "python:${PYTHON_VERSION}-slim-bookworm",
                "commands": EXPECTED_COMMANDS,
            }
        },
    }


def test_quality_pipeline_has_no_operational_capabilities() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))

    forbidden_keys = {"secrets", "privileged", "volumes", "services", "depends_on"}
    present_keys = {key for key, _value in _walk(workflow) if key is not None}
    assert forbidden_keys.isdisjoint(present_keys)

    commands = [
        value.lower() for key, value in _walk(workflow) if key is None and isinstance(value, str)
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
