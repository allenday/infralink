"""Generate the versioned Doctor adapter-binding contract schema."""

from __future__ import annotations

import json
from pathlib import Path

from infralink.cli.adapter_bindings import AdapterBindings

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "src" / "infralink" / "schemas" / "adapter-bindings"


def render_schemas() -> dict[str, str]:
    schema = AdapterBindings.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    return {"v2/document.json": json.dumps(schema, indent=2, sort_keys=True) + "\n"}


def write_schemas(output: Path = OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rendered = render_schemas()
    for path in output.rglob("*.json"):
        if path.relative_to(output).as_posix() not in rendered:
            path.unlink()
    for name, content in rendered.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    write_schemas()
