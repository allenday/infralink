"""Agent-facing contracts for the offline observation CLI."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Generic, Literal, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from infralink.cli.contracts import Action

T = TypeVar("T")


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
    diagnostics: dict[str, Any]


class ExplainResult(ObservationContract):
    code: str
    meaning: str
    affected_identity_types: list[str]
    likely_causes: list[str]
    next_actions: list[str]


class ProjectObservationResult(ObservationContract):
    plan: dict[str, Any]
    sources: list[dict[str, Any]]


class ProjectSecretsResult(ObservationContract):
    plan_digest: str
    secret_requirements: list[dict[str, Any]]
    secret_bindings: list[dict[str, Any]]
    provider_aliases: list[dict[str, Any]]
    opaque_identities: list[dict[str, Any]]


class ProjectViewResult(ObservationContract):
    plan_digest: str
    view: dict[str, Any]


class ProjectReadinessResult(ObservationContract):
    plan_digest: str
    readiness_suite: dict[str, Any]
