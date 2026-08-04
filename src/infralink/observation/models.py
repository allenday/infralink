"""Strict, offline source models for observation contracts."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

CanonicalId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"),
]
Port = Annotated[StrictInt, Field(ge=1, le=65535)]
PositiveSeconds = Annotated[StrictInt, Field(gt=0)]


class StrictModel(BaseModel):
    """Base for public source contracts, which never ignore input fields."""

    model_config = ConfigDict(extra="forbid", strict=True)


class EndpointProtocol(str, Enum):
    TCP = "tcp"
    HTTP = "http"
    HTTPS = "https"
    SMTP = "smtp"
    IRC = "irc"
    POSTGRESQL = "postgresql"


class HealthEvaluator(str, Enum):
    TCP_CONNECT = "tcp-connect"
    HTTP_STATUS = "http-status"
    SMTP_BANNER = "smtp-banner"
    IRC_HANDSHAKE = "irc-handshake"
    POSTGRES_READY = "postgres-ready"


class MetricsEvaluator(str, Enum):
    PROMETHEUS_SCRAPE = "prometheus-scrape"


class LogEvaluator(str, Enum):
    CONTAINS = "contains"
    REGEX = "regex"


class SignalEvaluator(str, Enum):
    CAPABILITY_STATE = "capability-state"
    METRIC_THRESHOLD = "metric-threshold"
    LOG_MATCH = "log-match"


class ComparisonOperator(str, Enum):
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"
    NE = "ne"


class SecretDeliveryForm(str, Enum):
    ENVIRONMENT = "environment"
    FILE = "file"
    STDIN = "stdin"


class SignalRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"


class SignalDisplay(str, Enum):
    STATUS = "status"
    VALUE = "value"
    RATE = "rate"


class SuitePolicy(str, Enum):
    MUST_PASS = "must-pass"
    SHOULD_PASS = "should-pass"


class BackendKind(str, Enum):
    HEALTH = "health"
    METRICS = "metrics"
    LOGS = "logs"


class WaiverScopeKind(str, Enum):
    SIGNAL = "signal"
    CAPABILITY = "capability"
    SUITE_MEMBER = "suite-member"


class Endpoint(StrictModel):
    """A generic named network endpoint exposed by a service."""

    id: CanonicalId
    protocol: EndpointProtocol
    port: Port
    address: str | None = None
    path: str | None = None


class HealthCapability(StrictModel):
    id: CanonicalId
    endpoint_id: CanonicalId
    evaluator: HealthEvaluator
    path: str | None = None
    expected_statuses: list[Annotated[StrictInt, Field(ge=100, le=599)]] = Field(
        default_factory=list
    )


class MetricsCapability(StrictModel):
    id: CanonicalId
    endpoint_id: CanonicalId
    evaluator: MetricsEvaluator
    path: str | None = None


class LogCapability(StrictModel):
    id: CanonicalId
    evaluator: LogEvaluator
    endpoint_id: CanonicalId | None = None
    stream: str | None = None


class MetricCondition(StrictModel):
    operator: ComparisonOperator
    threshold: float


class LogicalSignal(StrictModel):
    """A vendor-neutral assertion derived from one declared capability."""

    id: CanonicalId
    capability_id: CanonicalId
    evaluator: SignalEvaluator
    metric: str | None = None
    condition: MetricCondition | None = None
    pattern: str | None = None

    @model_validator(mode="after")
    def validate_evaluator_inputs(self) -> LogicalSignal:
        if self.evaluator == SignalEvaluator.METRIC_THRESHOLD:
            if self.metric is None or self.condition is None:
                raise ValueError("metric-threshold requires metric and condition")
        elif self.metric is not None or self.condition is not None:
            raise ValueError("metric and condition are only valid for metric-threshold")
        if self.evaluator == SignalEvaluator.LOG_MATCH:
            if self.pattern is None:
                raise ValueError("log-match requires pattern")
        elif self.pattern is not None:
            raise ValueError("pattern is only valid for log-match")
        return self


class SecretSlot(StrictModel):
    id: CanonicalId
    required: bool = True
    delivery_forms: Annotated[list[SecretDeliveryForm], Field(min_length=1)]
    purpose: Annotated[str, Field(min_length=1)]


class ServiceProfile(StrictModel):
    id: CanonicalId
    endpoints: list[Endpoint] = Field(default_factory=list)
    health: list[HealthCapability] = Field(default_factory=list)
    metrics: list[MetricsCapability] = Field(default_factory=list)
    logs: list[LogCapability] = Field(default_factory=list)
    signals: list[LogicalSignal] = Field(default_factory=list)
    secret_slots: list[SecretSlot] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_endpoint_references(self) -> ServiceProfile:
        endpoint_ids = {endpoint.id for endpoint in self.endpoints}
        references = [(capability.id, capability.endpoint_id) for capability in self.health]
        references.extend(
            (capability.id, capability.endpoint_id) for capability in self.metrics
        )
        references.extend(
            (capability.id, capability.endpoint_id)
            for capability in self.logs
            if capability.endpoint_id is not None
        )
        for capability_id, endpoint_id in references:
            if endpoint_id not in endpoint_ids:
                raise ValueError(
                    f"capability {capability_id!r} references missing endpoint {endpoint_id!r}"
                )
        return self


class ServiceInstance(StrictModel):
    id: CanonicalId
    profile_id: CanonicalId
    endpoint_ids: list[CanonicalId] = Field(default_factory=list)
    secret_binding_ids: list[CanonicalId] = Field(default_factory=list)


_SENSITIVE_KEY = re.compile(r"(?:^|[^a-z])(value|secret|password|token)(?:$|[^a-z])")
_SENSITIVE_VALUE = re.compile(
    r"(?:password|passwd|secret|token|api[_-]?key)\s*[:=]", re.IGNORECASE
)


def _contains_inline_secret(value: object) -> bool:
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = re.sub(r"(?<!^)(?=[A-Z])", "_", str(key)).lower()
            normalized_key = re.sub(r"[_-]+", " ", normalized_key)
            if _SENSITIVE_KEY.search(normalized_key) or _contains_inline_secret(child):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_inline_secret(child) for child in value)
    return isinstance(value, str) and _SENSITIVE_VALUE.search(value) is not None


class SecretBinding(StrictModel):
    id: CanonicalId
    slot_id: CanonicalId
    provider: CanonicalId
    provider_ref: CanonicalId
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("provider_ref")
    @classmethod
    def reject_secret_like_provider_ref(cls, value: str) -> str:
        if any(part in {"value", "secret", "password", "token"} for part in value.split("-")):
            raise ValueError("provider_ref must not contain secret-like terms")
        return value

    @field_validator("metadata")
    @classmethod
    def reject_inline_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _contains_inline_secret(value):
            raise ValueError("provider binding metadata contains inline secret material")
        return value


class ProviderAlias(StrictModel):
    id: CanonicalId
    provider: Annotated[str, Field(min_length=1)]


class DependencyContract(StrictModel):
    id: CanonicalId
    source_service_id: CanonicalId
    target_service_id: CanonicalId
    required: bool = True
    health_signal_refs: list[CanonicalId] = Field(default_factory=list)


class Application(StrictModel):
    id: CanonicalId
    service_instance_ids: Annotated[list[CanonicalId], Field(min_length=1)]
    required_dependency_edge_ids: list[CanonicalId] = Field(default_factory=list)
    health_signal_refs: list[CanonicalId] = Field(default_factory=list)


class RendererBindingIdentity(StrictModel):
    id: CanonicalId
    renderer: Annotated[str, Field(min_length=1)]
    binding_ref: Annotated[str, Field(min_length=1)]


class ObservationBackend(StrictModel):
    id: CanonicalId
    kind: BackendKind
    backend_ref: Annotated[str, Field(min_length=1)]


class DatasourceBinding(StrictModel):
    id: CanonicalId
    backend_id: CanonicalId
    datasource_ref: Annotated[str, Field(min_length=1)]
    observed_at: datetime | None = None


class WaiverScope(StrictModel):
    """Stable identity of the exact contract member waived."""

    kind: WaiverScopeKind
    ref: CanonicalId
    suite_ref: CanonicalId | None = None

    @model_validator(mode="after")
    def validate_suite_qualification(self) -> WaiverScope:
        if self.kind == WaiverScopeKind.SUITE_MEMBER and self.suite_ref is None:
            raise ValueError("suite-member scope requires suite_ref")
        if self.kind != WaiverScopeKind.SUITE_MEMBER and self.suite_ref is not None:
            raise ValueError("suite_ref is only valid for suite-member scope")
        return self


class Waiver(StrictModel):
    id: CanonicalId
    scope: WaiverScope
    owner: Annotated[str, Field(min_length=1)]
    reason: Annotated[str, Field(min_length=1)]
    created_on: date
    expires_on: date

    @model_validator(mode="after")
    def validate_expiry(self) -> Waiver:
        if self.expires_on <= self.created_on:
            raise ValueError("waiver expiry must be after creation")
        return self


class SignalMembership(StrictModel):
    signal_ref: CanonicalId
    requirement: SignalRequirement
    display: SignalDisplay = SignalDisplay.STATUS
    label: str | None = None


class OperationsView(StrictModel):
    id: CanonicalId
    title: Annotated[str, Field(min_length=1)]
    signals: list[SignalMembership] = Field(default_factory=list)


class SuiteMember(StrictModel):
    signal_ref: CanonicalId
    policy: SuitePolicy
    cadence_seconds: PositiveSeconds
    continuity_seconds: Annotated[StrictInt, Field(ge=0)]
    freshness_seconds: PositiveSeconds


class ReadinessSuite(StrictModel):
    id: CanonicalId
    members: Annotated[list[SuiteMember], Field(min_length=1)]
