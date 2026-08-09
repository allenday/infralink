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
    DoctorResult,
    EdgeListResult,
    EdgeShowResult,
    Envelope,
    HelpResult,
    HostListResult,
    HostShowResult,
    InfoResult,
    PublisherRequestResult,
    ReleaseAttestationResult,
    ReleaseCandidateResult,
    ReleaseInspectResult,
    ResolveResult,
    RootResult,
    SecretsAuditResult,
    SecretsInspectResult,
    ServiceListResult,
    ServiceShowResult,
    ValidateResult,
    VersionResult,
)
from infralink.cli.observation_contracts import (
    CapabilitiesResult,
    ExplainResult,
    ObservationEnvelope,
    ObservationValidateResult,
    ProjectObservationResult,
    ProjectReadinessResult,
    ProjectSecretsResult,
    ProjectViewResult,
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
    "doctor": Envelope[DoctorResult],
    "app-list": Envelope[AppListResult],
    "app-show": Envelope[AppShowResult],
    "analyze": Envelope[AnalyzeResult],
    "diagram": Envelope[ArtifactResult],
    "docs": Envelope[ArtifactResult],
    "secrets-inspect": Envelope[SecretsInspectResult],
    "secrets-audit": Envelope[SecretsAuditResult],
    "release-inspect": Envelope[ReleaseInspectResult],
    "release-validate-candidate": Envelope[ReleaseCandidateResult],
    "release-render-publisher-request": Envelope[PublisherRequestResult],
    "release-inspect-attestation": Envelope[ReleaseAttestationResult],
    "capabilities": ObservationEnvelope[CapabilitiesResult],
    "observation-validate": ObservationEnvelope[ObservationValidateResult],
    "explain": ObservationEnvelope[ExplainResult],
    "project-observation": ObservationEnvelope[ProjectObservationResult],
    "project-secrets": ObservationEnvelope[ProjectSecretsResult],
    "project-view": ObservationEnvelope[ProjectViewResult],
    "project-readiness": ObservationEnvelope[ProjectReadinessResult],
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


def render_schemas() -> dict[str, str]:
    rendered_schemas: dict[str, str] = {}
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        for definition in schema.get("$defs", {}).values():
            if definition.get("title") not in {"Action", "HelpNavigationAction"}:
                continue
            definition.get("properties", {}).pop("argv", None)
            if "required" in definition:
                definition["required"] = [
                    field for field in definition["required"] if field != "argv"
                ]
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        schema["oneOf"] = OUTCOME_INVARIANT
        rendered_schemas[f"{name}.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return rendered_schemas


def write_schemas(output: Path = OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rendered_schemas = render_schemas()
    for schema_path in output.glob("*.json"):
        if schema_path.name not in rendered_schemas:
            schema_path.unlink()
    for filename, rendered in rendered_schemas.items():
        (output / filename).write_text(rendered, encoding="utf-8")


def main() -> None:
    write_schemas()


if __name__ == "__main__":
    main()
