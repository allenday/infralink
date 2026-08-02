from __future__ import annotations

import json
import os
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def source_checkout_env() -> dict[str, str]:
    """Return an explicit subprocess environment for source-tree tests."""
    env = os.environ.copy()
    source = str(ROOT / "src")
    inherited = env.get("PYTHONPATH")
    env["PYTHONPATH"] = os.pathsep.join((source, inherited)) if inherited else source
    return env


def assert_schema(payload: dict, name: str) -> None:
    schema = json.loads(
        (ROOT / "src/infralink/schemas/cli/v1" / f"{name}.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)
