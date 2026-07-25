from __future__ import annotations

import json
from pathlib import Path

from jsonschema import Draft202012Validator

ROOT = Path(__file__).resolve().parents[1]


def assert_schema(payload: dict, name: str) -> None:
    schema = json.loads(
        (ROOT / "src/infralink/schemas/cli/v1" / f"{name}.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(payload)
