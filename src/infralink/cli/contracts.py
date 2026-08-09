"""Versioned, machine-readable CLI response contracts."""

from __future__ import annotations

from pathlib import PurePath
from typing import Annotated, Any, Generic, Literal, TypeVar, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SerializerFunctionWrapHandler,
    field_validator,
    model_serializer,
    model_validator,
)

from infralink.release.contracts import (
    PublisherRequestV2,
    PublisherRequestV3,
    ReleaseAttestationV2,
    ReleaseAttestationV3,
)

T = TypeVar("T")


class ContractModel(BaseModel):
    """Base contract that rejects fields outside the published schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)


class PageInfo(ContractModel):
    limit: int = Field(ge=1, le=1000)
    returned: int = Field(ge=0)
    total: int | None = Field(default=None, ge=0)
    next_cursor: str | None = None


class Page(ContractModel, Generic[T]):
    items: list[T]
    page: PageInfo


class Binding(ContractModel):
    type: Literal["string", "integer", "boolean"]
    required: bool
    source: str


class Action(ContractModel):
    rel: str
    argv: list[str]
    command: str
    description: str
    safe: bool
    templated: bool = False
    bindings: dict[str, Binding] = Field(default_factory=dict)


class CommandContext(ContractModel):
    raw: str
    parsed: dict[str, Any]
    resolved: dict[str, Any]


class ErrorDetail(ContractModel):
    code: str
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class Meta(ContractModel):
    truncated: bool = False


class Envelope(ContractModel, Generic[T]):
    schema_version: Literal["infralink.cli/v1"] = "infralink.cli/v1"
    ok: bool
    command: CommandContext
    result: T | None = None
    error: ErrorDetail | None = None
    fix: str | None = None
    next_actions: list[Action]
    meta: Meta = Field(default_factory=Meta)

    @model_validator(mode="after")
    def enforce_outcome(self) -> Envelope[T]:
        success = (
            self.ok
            and self.result is not None
            and self.error is None
            and "error" not in self.model_fields_set
        )
        failure = (
            not self.ok
            and self.result is None
            and self.error is not None
            and "result" not in self.model_fields_set
        )
        if not (success or failure):
            raise ValueError("ok must select exactly one of result or error")
        return self

    @model_serializer(mode="wrap")
    def serialize_outcome(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        serialized = cast(dict[str, Any], handler(self))
        if self.ok:
            serialized.pop("error", None)
        else:
            serialized.pop("result", None)
        return serialized


class Diagnostic(ContractModel):
    code: str
    path: str | None = None
    message: str
    severity: Literal["error", "warning"]


class HostSummary(ContractModel):
    id: str
    canonical_name: str
    status: str
    service_count: int
    services: list[str] = Field(max_length=128)
    services_truncated: bool
    project_count: int
    projects: list[str] = Field(max_length=64)
    projects_truncated: bool


class EdgeSummary(ContractModel):
    id: str
    type: str
    from_: dict[str, Any] = Field(alias="from")
    to: dict[str, Any]
    protocol: str | None
    secret_ref_count: int
    secret_refs: list[str] = Field(max_length=32)
    secret_refs_truncated: bool


class Endpoint(ContractModel):
    host: str
    port: int
    protocol: str | None


class CheckResult(ContractModel):
    edge_id: str
    healthy: bool
    status: str
    latency_ms: float | None
    error_code: str | None


class AppSummary(ContractModel):
    id: str
    service_count: int
    edge_count: int


class ServiceSummary(ContractModel):
    id: str
    host_count: int
    host_ids: list[str] = Field(max_length=128)
    hosts_truncated: bool
    port_count: int
    ports: list[int] = Field(max_length=64)
    ports_truncated: bool
    protocol_count: int
    protocols: list[str] = Field(max_length=32)
    protocols_truncated: bool


class SourceLocation(ContractModel):
    source: str
    path: str


class SecretReferenceStatus(ContractModel):
    ref: str
    location_count: int
    location_preview: list[SourceLocation] = Field(max_length=16)
    locations_truncated: bool
    project: str | None
    present: bool | None
    accessible: bool | None
    error_code: str | None


class Artifact(ContractModel):
    path: str = Field(min_length=1)
    media_type: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePath(value)
        if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
            raise ValueError("artifact path must be safe and relative")
        return value


class ReleaseFacts(ContractModel):
    identity: str = Field(pattern=r"^releases/[a-z0-9][a-z0-9-]{0,62}/[1-9][0-9]*$")
    registry_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    annotated: bool
    status: Literal["active", "revoked"]


class ReleaseSelection(ContractModel):
    mode: Literal["release-channel", "raw-revision"]
    channel: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    recent_window: int | None = Field(default=None, ge=1, le=256)
    maximum_candidates: int | None = Field(default=None, ge=1, le=32)
    registry_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")


class ReleaseAdmission(ContractModel):
    state: Literal["admitted", "not-admitted"]
    selection: ReleaseSelection
    reason: Literal["revoked"] | None = None

    @model_validator(mode="after")
    def require_reason_for_non_admission(self) -> ReleaseAdmission:
        if (self.state == "not-admitted") != (self.reason is not None):
            raise ValueError("non-admitted release requires a reason")
        return self


class ReleasePublisher(ContractModel):
    state: Literal["unavailable", "eligible"]
    provider: str | None = None


class ReleaseProvenance(ContractModel):
    validation_schema_version: Literal["infralink.release-validation.v1"]
    source: Literal["release-validation"]


class ReleaseCompatibility(ContractModel):
    selection_mode: Literal["release-channel", "raw-revision"]
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class ReleaseInspectResult(ContractModel):
    release: ReleaseFacts
    admission: ReleaseAdmission
    publisher: ReleasePublisher
    provenance: ReleaseProvenance
    compatibility: ReleaseCompatibility


class ReleaseCiReceipt(ContractModel):
    provider: str = Field(min_length=1, max_length=128)
    repository: str = Field(min_length=1, max_length=256)
    run: str = Field(min_length=1, max_length=128)


class ReleaseArtifactBinding(ContractModel):
    path: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


ReleaseConsumer = Annotated[str, Field(min_length=1, max_length=128)]


class ReleaseCandidate(ContractModel):
    identity: str = Field(pattern=r"^releases/[a-z0-9][a-z0-9-]{0,62}/[1-9][0-9]*$")
    registry_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    ci_receipt: ReleaseCiReceipt
    artifacts: list[ReleaseArtifactBinding] = Field(min_length=1, max_length=64)
    consumers: list[ReleaseConsumer] = Field(min_length=1, max_length=64)


class ReleaseCandidateResult(ContractModel):
    candidate: ReleaseCandidate


class PublisherRequest(ContractModel):
    schema_version: Literal["infralink.publisher-request.v1"]
    release_identity: str = Field(pattern=r"^releases/[a-z0-9][a-z0-9-]{0,62}/[1-9][0-9]*$")
    channel: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    sequence: int = Field(ge=1)
    registry_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    ci_receipt: ReleaseCiReceipt
    artifacts: list[ReleaseArtifactBinding] = Field(min_length=1, max_length=64)
    consumers: list[ReleaseConsumer] = Field(min_length=1, max_length=64)


class PublisherRequestResult(ContractModel):
    publisher_request: PublisherRequest | PublisherRequestV2 | PublisherRequestV3


class ReleasePublisherReceipt(ContractModel):
    provider: str = Field(min_length=1, max_length=128)
    repository: str | None = Field(default=None, min_length=1, max_length=256)
    run: str = Field(min_length=1, max_length=128)


class ReleaseAttestation(ContractModel):
    release_identity: str = Field(pattern=r"^releases/[a-z0-9][a-z0-9-]{0,62}/[1-9][0-9]*$")
    registry_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    ci_receipt: ReleaseCiReceipt | None = None
    artifacts: list[ReleaseArtifactBinding] = Field(default_factory=list, max_length=64)
    publisher_receipt: ReleasePublisherReceipt
    tag: str = Field(pattern=r"^releases/[a-z0-9][a-z0-9-]{0,62}/[1-9][0-9]*$")
    tag_object_sha1: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    consumers: list[ReleaseConsumer] = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def tag_matches_release(self) -> ReleaseAttestation:
        if self.tag != self.release_identity:
            raise ValueError("tag must match release identity")
        return self


class ReleaseAttestationResult(ContractModel):
    attestation: ReleaseAttestation | ReleaseAttestationV2 | ReleaseAttestationV3


class CommandDescriptor(ContractModel):
    name: str
    description: str
    usage: str


class ArgumentDescriptor(ContractModel):
    name: str
    type: str
    required: bool


class OptionDescriptor(ContractModel):
    name: str
    type: str
    required: bool


class InfoSources(ContractModel):
    registry: str
    edges: str


class InfoSummary(ContractModel):
    host_count: int
    service_count: int
    edge_count: int


class ValidationSummary(ContractModel):
    error_count: int
    warning_count: int


class CheckSummary(ContractModel):
    total: int
    healthy: int
    unhealthy: int


class AnalysisSummary(ContractModel):
    host_count: int
    service_count: int
    edge_count: int
    diagnostics: Page[Diagnostic]


class ArtifactSummary(ContractModel):
    artifact_count: int


class SecretsSummary(ContractModel):
    total: int
    present: int
    missing: int
    accessible: int
    denied: int


class RootResult(ContractModel):
    version: str
    commands: list[CommandDescriptor]


class HelpResult(ContractModel):
    path: list[str]
    description: str
    arguments: list[ArgumentDescriptor]
    options: list[OptionDescriptor]
    examples: list[str]
    children: list[HelpSubcommand] = Field(default_factory=list)


class HelpSubcommand(ContractModel):
    name: str
    summary: str
    action: HelpNavigationAction


class HelpNavigationAction(ContractModel):
    rel: Literal["help"] = "help"
    command: str


class VersionResult(ContractModel):
    version: str
    cli_schema_version: Literal["infralink.cli/v1"]


class InfoResult(ContractModel):
    sources: InfoSources
    summary: InfoSummary


class HostListResult(ContractModel):
    items: list[HostSummary]
    page: PageInfo


class HostShowResult(ContractModel):
    host: HostSummary
    services: Page[str]
    projects: Page[str]


class ServiceListResult(ContractModel):
    items: list[ServiceSummary]
    page: PageInfo


class ServiceShowResult(ContractModel):
    service: ServiceSummary
    hosts: Page[str]
    ports: Page[int]
    protocols: Page[str]
    edges: Page[EdgeSummary]


class EdgeListResult(ContractModel):
    items: list[EdgeSummary]
    page: PageInfo


class EdgeShowResult(ContractModel):
    edge: EdgeSummary
    secret_refs: Page[str]


class ValidateResult(ContractModel):
    valid: bool
    errors: Page[Diagnostic]
    warnings: Page[Diagnostic]
    summary: ValidationSummary


class ResolveResult(ContractModel):
    edge: EdgeSummary
    endpoint: Endpoint
    connection_template: str | None
    secret_refs: Page[str]


class CheckCommandResult(ContractModel):
    healthy: bool
    checks: Page[CheckResult]
    summary: CheckSummary


class AppListResult(ContractModel):
    items: list[AppSummary]
    page: PageInfo


class AppShowResult(ContractModel):
    app: AppSummary
    services: Page[ServiceSummary]
    edges: Page[EdgeSummary]


class AnalyzeResult(ContractModel):
    analysis: AnalysisSummary
    artifacts: Page[Artifact]


class ArtifactResult(ContractModel):
    artifacts: Page[Artifact]
    summary: ArtifactSummary


class SecretsInspectResult(ContractModel):
    references: Page[SecretReferenceStatus]
    locations: Page[SourceLocation]
    summary: SecretsSummary


class SecretsAuditResult(ContractModel):
    provider: str
    references: Page[SecretReferenceStatus]
    summary: SecretsSummary
