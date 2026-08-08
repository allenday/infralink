"""Generate packaged public release producer schemas deterministically."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infralink.release.contracts import ReleaseAttestationV1, ReleaseCandidateV1

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "src" / "infralink" / "schemas" / "release" / "v1"
MODELS: dict[str, Any] = {
    "release-candidate.v1.schema.json": ReleaseCandidateV1,
    "release-attestation.v1.schema.json": ReleaseAttestationV1,
}


def render_schemas() -> dict[str, str]:
    rendered: dict[str, str] = {}
    for filename, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        rendered[filename] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return rendered


def write_schemas(output: Path = OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rendered = render_schemas()
    for path in output.glob("*.json"):
        if path.name not in rendered:
            path.unlink()
    for filename, body in rendered.items():
        (output / filename).write_text(body, encoding="utf-8")


if __name__ == "__main__":
    write_schemas()
