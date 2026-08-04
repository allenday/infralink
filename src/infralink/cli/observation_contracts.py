"""Agent-facing contracts for the offline observation CLI."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from infralink.cli.contracts import Action
from infralink.observation.planner import (
    OpaqueIdentity,
    Plan,
    PlannedAlias,
    PlannedOperationsView,
    PlannedReadinessSuite,
    PlannedSecretBinding,
    PlannedSecretRequirement,
)

T = TypeVar("T")
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class ObservationContract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ObservationCommand(ObservationContract):
    raw_redacted: str
    parsed: dict[str, Any]
    resolved: dict[str, Any]


class ObservationError(ObservationContract):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ObservationMeta(ObservationContract):
    truncated: bool = False


class ObservationEnvelope(ObservationContract, Generic[T]):
    schema_version: Literal["agent-cli.response.v1"] = "agent-cli.response.v1"
    request_id: str
    generated_at: datetime
    ok: bool
    command: ObservationCommand
    result: T | None = None
    error: ObservationError | None = None
    next_actions: list[Action] = Field(default_factory=list)
    meta: ObservationMeta = Field(default_factory=ObservationMeta)

    @model_validator(mode="after")
    def exactly_one_outcome(self) -> ObservationEnvelope[T]:
        if self.ok != (self.result is not None) or self.ok == (self.error is not None):
            raise ValueError("ok must select exactly one of result or error")
        return self

    @model_serializer(mode="wrap")
    def serialize_outcome(self, handler: Any) -> dict[str, Any]:
        value = cast(dict[str, Any], handler(self))
        value.pop("error" if self.ok else "result", None)
        return value


class CapabilitiesResult(ObservationContract):
    document_schema_versions: list[str]
    plan_schema_versions: list[str]
    input_schemas: dict[str, str]
    evaluator_types: dict[str, list[str]]
    projections: list[str]


class ObservationValidateResult(ObservationContract):
    valid: bool
    document_count: int
    diagnostics: DiagnosticSetResult


class SourceLocationResult(ObservationContract):
    path: str
    pointer: str
    document_index: int


class DiagnosticResult(ObservationContract):
    code: str
    severity: Literal["error", "warning"]
    message: str
    location: SourceLocationResult
    identity: str | None = None
    next_actions: tuple[str, ...] = ()


class DiagnosticSetResult(ObservationContract):
    diagnostics: tuple[DiagnosticResult, ...]
    limit: int = Field(ge=0)
    total_count: int = Field(ge=0)
    truncated: bool
    error_count: int = Field(ge=0)
    warning_count: int = Field(ge=0)


class ExplainResult(ObservationContract):
    code: str
    meaning: str
    affected_identity_types: list[str]
    likely_causes: list[str]
    next_actions: list[str]


class ProjectObservationResult(ObservationContract):
    plan: ObservationPlan
    sources: tuple[SourceProvenanceResult, ...]


class SourceProvenanceResult(ObservationContract):
    path: str
    document_index: int = Field(ge=0)
    raw_sha256: Sha256
    semantic_sha256: Sha256


class ObservationPlan(Plan):
    document_digests: tuple[Sha256, ...]
    plan_digest: Sha256


class ObservationReadinessSuite(PlannedReadinessSuite):
    suite_digest: Sha256
    scoped_plan_digest: Sha256


class ProjectSecretsResult(ObservationContract):
    plan_digest: Sha256
    secret_requirements: tuple[PlannedSecretRequirement, ...]
    secret_bindings: tuple[PlannedSecretBinding, ...]
    provider_aliases: tuple[PlannedAlias, ...]
    opaque_identities: tuple[OpaqueIdentity, ...]


class ProjectViewResult(ObservationContract):
    plan_digest: Sha256
    view: PlannedOperationsView


class ProjectReadinessResult(ObservationContract):
    plan_digest: Sha256
    readiness_suite: ObservationReadinessSuite
