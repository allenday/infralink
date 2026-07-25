from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from infralink.cli.contracts import (
    AnalyzeResult,
    AppListResult,
    AppShowResult,
    ArtifactResult,
    CheckCommandResult,
    EdgeListResult,
    EdgeShowResult,
    Envelope,
    HelpResult,
    HostListResult,
    HostShowResult,
    InfoResult,
    ResolveResult,
    RootResult,
    SecretsAuditResult,
    SecretsInspectResult,
    ServiceListResult,
    ServiceShowResult,
    ValidateResult,
    VersionResult,
)

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "src/infralink/schemas/cli/v1"
MODELS: dict[str, Any] = {
    "root": Envelope[RootResult],
    "help": Envelope[HelpResult],
    "version": Envelope[VersionResult],
    "info": Envelope[InfoResult],
    "hosts": Envelope[HostListResult],
    "host-show": Envelope[HostShowResult],
    "services": Envelope[ServiceListResult],
    "service-show": Envelope[ServiceShowResult],
    "edges-list": Envelope[EdgeListResult],
    "edge-show": Envelope[EdgeShowResult],
    "validate": Envelope[ValidateResult],
    "resolve": Envelope[ResolveResult],
    "check": Envelope[CheckCommandResult],
    "app-list": Envelope[AppListResult],
    "app-show": Envelope[AppShowResult],
    "analyze": Envelope[AnalyzeResult],
    "diagram": Envelope[ArtifactResult],
    "docs": Envelope[ArtifactResult],
    "secrets-inspect": Envelope[SecretsInspectResult],
    "secrets-audit": Envelope[SecretsAuditResult],
}

OUTCOME_INVARIANT = [
    {
        "properties": {
            "ok": {"const": True},
            "result": {"not": {"type": "null"}},
        },
        "required": ["ok", "result"],
        "not": {"required": ["error"]},
    },
    {
        "properties": {
            "ok": {"const": False},
            "error": {"not": {"type": "null"}},
        },
        "required": ["ok", "error"],
        "not": {"required": ["result"]},
    },
]


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["oneOf"] = OUTCOME_INVARIANT
        rendered = json.dumps(schema, indent=2, sort_keys=True) + "\n"
        (OUTPUT / f"{name}.json").write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
