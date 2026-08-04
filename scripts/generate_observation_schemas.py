from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, create_model

from infralink.observation.models import (
    Application,
    DatasourceBinding,
    DependencyContract,
    Host,
    ObservationBackend,
    OperationsView,
    ProviderAlias,
    ReadinessSuite,
    RendererBindingIdentity,
    SecretBinding,
    ServiceInstance,
    ServiceProfile,
    Waiver,
)

ROOT = Path(__file__).parents[1]
OUTPUT = ROOT / "src/infralink/schemas/observation/v1"
Version = Literal["infralink.observation/v1"]


def _document(name: str, **sections: tuple[Any, Any]) -> type[BaseModel]:
    fields: dict[str, Any | tuple[Any, Any]] = {
        "schema_version": (Version, ...),
        "registry_revision": (str | None, None),
        **sections,
    }
    return create_model(
        name,
        __config__=ConfigDict(extra="forbid", strict=True),
        **fields,  # type: ignore[arg-type]
    )


MODELS = {
    "profile": _document("ProfileDocument", service_profiles=(list[ServiceProfile], ...)),
    "instance": _document(
        "InstanceDocument",
        hosts=(list[Host], Field(default_factory=list)),
        service_instances=(list[ServiceInstance], ...),
        applications=(list[Application], Field(default_factory=list)),
    ),
    "application": _document("ApplicationDocument", applications=(list[Application], ...)),
    "dependency": _document(
        "DependencyDocument", dependency_contracts=(list[DependencyContract], ...)
    ),
    "secrets": _document(
        "SecretsDocument",
        provider_aliases=(list[ProviderAlias], Field(default_factory=list)),
        secret_bindings=(list[SecretBinding], Field(default_factory=list)),
        renderer_binding_identities=(list[RendererBindingIdentity], Field(default_factory=list)),
        renderer_bindings=(list[RendererBindingIdentity], Field(default_factory=list)),
    ),
    "operations-view": _document(
        "OperationsViewDocument",
        observation_backends=(list[ObservationBackend], Field(default_factory=list)),
        datasource_bindings=(list[DatasourceBinding], Field(default_factory=list)),
        operations_views=(list[OperationsView], ...),
        waivers=(list[Waiver], Field(default_factory=list)),
        readiness_suites=(list[ReadinessSuite], Field(default_factory=list)),
    ),
    "readiness-suite": _document(
        "ReadinessSuiteDocument", readiness_suites=(list[ReadinessSuite], ...)
    ),
}


def render_schemas() -> dict[str, str]:
    rendered = {}
    for name, model in MODELS.items():
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        rendered[f"{name}.json"] = json.dumps(schema, indent=2, sort_keys=True) + "\n"
    return rendered


def write_schemas(output: Path = OUTPUT) -> None:
    output.mkdir(parents=True, exist_ok=True)
    rendered = render_schemas()
    for path in output.glob("*.json"):
        if path.name not in rendered:
            path.unlink()
    for name, content in rendered.items():
        (output / name).write_text(content, encoding="utf-8")


if __name__ == "__main__":
    write_schemas()
