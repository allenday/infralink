"""Generate packaged public release producer schemas deterministically."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infralink.release.contracts import (
    PublisherRequestV2,
    PublisherRequestV3,
    ReleaseAttestationV1,
    ReleaseAttestationV2,
    ReleaseAttestationV3,
    ReleaseCandidateV1,
)

ROOT = Path(__file__).parents[1]
OUTPUT_ROOT = ROOT / "src" / "infralink" / "schemas" / "release"
V1_MODELS: dict[str, Any] = {
    "release-candidate.v1.schema.json": ReleaseCandidateV1,
    "release-attestation.v1.schema.json": ReleaseAttestationV1,
}
V2_MODELS: dict[str, Any] = {
    "publisher-request.v2.schema.json": PublisherRequestV2,
    "release-attestation.v2.schema.json": ReleaseAttestationV2,
}
V3_MODELS: dict[str, Any] = {
    "publisher-request.v3.schema.json": PublisherRequestV3,
    "release-attestation.v3.schema.json": ReleaseAttestationV3,
}


def render_schemas(models: dict[str, Any] = V1_MODELS) -> dict[str, str]:
    rendered: dict[str, str] = {}
    for filename, model in models.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        rendered[filename] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return rendered


def write_schemas(output_root: Path = OUTPUT_ROOT) -> None:
    for version, models in (("v1", V1_MODELS), ("v2", V2_MODELS), ("v3", V3_MODELS)):
        output = output_root / version
        output.mkdir(parents=True, exist_ok=True)
        rendered = render_schemas(models)
        for path in output.glob("*.json"):
            if path.name not in rendered:
                path.unlink()
        for filename, body in rendered.items():
            (output / filename).write_text(body, encoding="utf-8")


if __name__ == "__main__":
    write_schemas()
