"""Strict, offline source models for observation contracts."""

from __future__ import annotations

import re
from datetime import date, datetime
from enum import Enum
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)


def _validate_uuid(value: str) -> str:
    try:
        parsed = UUID(value)
    except (ValueError, AttributeError) as error:
        raise ValueError("host id must be a UUID") from error
    if value != str(parsed):
        raise ValueError("host id must use canonical lowercase hyphenated UUID spelling")
    return value


CanonicalId = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"),
]
QualifiedRef = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9/-]*[a-z0-9])?$"),
]
HostId = Annotated[str, AfterValidator(_validate_uuid)]
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
    REDIS = "redis"


class HealthEvaluator(str, Enum):
    TCP_CONNECT = "tcp-connect"
    HTTP_STATUS = "http-status"
    SMTP_BANNER = "smtp-banner"
    IRC_HANDSHAKE = "irc-handshake"
    POSTGRES_READY = "postgres-ready"
    REDIS_READY = "redis-ready"


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


class EndpointExposure(str, Enum):
    PRIVATE = "private"
    PUBLIC = "public"


class RuntimeNetworkScope(str, Enum):
    """The service network namespace derived from its registry Compose declaration."""

    BRIDGE = "bridge"
    HOST = "host"


class SignalRequirement(str, Enum):
    REQUIRED = "required"
    OPTIONAL = "optional"


class SignalDisplay(str, Enum):
    STATUS = "status"
    VALUE = "value"
    RATE = "rate"


class HostBaselineCapability(str, Enum):
    SELF_DEPLOY_AUTHORITY = "self-deploy-authority"
    DOCKER = "docker"
    HOST_METRICS = "host-metrics"
    CONTAINER_METRICS = "container-metrics"
    LOG_FORWARDING = "log-forwarding"


class FailurePolicy(str, Enum):
    FAIL = "fail"


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
    exposure: EndpointExposure | None = None
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
    threshold: Annotated[float, Field(allow_inf_nan=False)]


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


class LogicalServiceComponent(StrictModel):
    """One required same-host component of an operator-facing service."""

    profile_id: CanonicalId
    signal_id: CanonicalId


class LogicalService(StrictModel):
    """A profile-owned aggregate of ordinary component profiles."""

    id: CanonicalId
    display_name: Annotated[str, Field(min_length=1)] | None = None
    components: Annotated[list[LogicalServiceComponent], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicate_component_profiles(self) -> LogicalService:
        profile_ids = [component.profile_id for component in self.components]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("duplicate logical service component profile")
        return self


class ServiceProfile(StrictModel):
    id: CanonicalId
    display_name: Annotated[str, Field(min_length=1)] | None = None
    endpoints: list[Endpoint] = Field(default_factory=list)
    health: list[HealthCapability] = Field(default_factory=list)
    metrics: list[MetricsCapability] = Field(default_factory=list)
    logs: list[LogCapability] = Field(default_factory=list)
    signals: list[LogicalSignal] = Field(default_factory=list)
    secret_slots: list[SecretSlot] = Field(default_factory=list)
    required_host_baseline_capabilities: list[HostBaselineCapability] = Field(default_factory=list)
    logical_service: LogicalService | None = None

    @model_validator(mode="after")
    def validate_endpoint_references(self) -> ServiceProfile:
        if len(self.required_host_baseline_capabilities) != len(
            set(self.required_host_baseline_capabilities)
        ):
            raise ValueError("duplicate host baseline capability in service profile")
        if self.logical_service is not None and self.id not in {
            component.profile_id for component in self.logical_service.components
        }:
            raise ValueError("logical service must include its owning profile")
        endpoint_id_list = [endpoint.id for endpoint in self.endpoints]
        if len(endpoint_id_list) != len(set(endpoint_id_list)):
            raise ValueError("duplicate endpoint id in service profile")

        health_ids = {capability.id for capability in self.health}
        metrics_ids = {capability.id for capability in self.metrics}
        log_ids = {capability.id for capability in self.logs}
        capability_id_list = [
            *(capability.id for capability in self.health),
            *(capability.id for capability in self.metrics),
            *(capability.id for capability in self.logs),
        ]
        if len(capability_id_list) != len(set(capability_id_list)):
            raise ValueError("duplicate capability id in service profile")

        signal_id_list = [signal.id for signal in self.signals]
        if len(signal_id_list) != len(set(signal_id_list)):
            raise ValueError("duplicate signal id in service profile")

        endpoint_ids = set(endpoint_id_list)
        references = [(capability.id, capability.endpoint_id) for capability in self.health]
        references.extend((capability.id, capability.endpoint_id) for capability in self.metrics)
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

        expected_capability_ids = {
            SignalEvaluator.CAPABILITY_STATE: health_ids,
            SignalEvaluator.METRIC_THRESHOLD: metrics_ids,
            SignalEvaluator.LOG_MATCH: log_ids,
        }
        capability_labels = {
            SignalEvaluator.CAPABILITY_STATE: "health",
            SignalEvaluator.METRIC_THRESHOLD: "metrics",
            SignalEvaluator.LOG_MATCH: "logs",
        }
        all_capability_ids = health_ids | metrics_ids | log_ids
        for signal in self.signals:
            if signal.capability_id not in all_capability_ids:
                raise ValueError(
                    f"signal {signal.id!r} references missing capability {signal.capability_id!r}"
                )
            if signal.capability_id not in expected_capability_ids[signal.evaluator]:
                label = capability_labels[signal.evaluator]
                raise ValueError(f"signal {signal.id!r} must reference a {label} capability")
        return self


class EndpointOverride(StrictModel):
    endpoint_id: CanonicalId
    address: str | None = None
    exposure: EndpointExposure | None = None
    route: str | None = None


class ServiceInstance(StrictModel):
    id: CanonicalId
    host_id: HostId | None = None
    profile_id: CanonicalId
    display_name: Annotated[str, Field(min_length=1)] | None = None
    endpoint_ids: list[CanonicalId] = Field(default_factory=list)
    endpoint_overrides: list[EndpointOverride] = Field(default_factory=list)
    secret_binding_ids: list[CanonicalId] = Field(default_factory=list)
    network_scope: RuntimeNetworkScope | None = None


class HostBridgeIngress(StrictModel):
    """One registry-declared bridge-to-host firewall allowance."""

    service_instance_id: CanonicalId
    protocol: Literal["tcp", "udp"]
    port: Port


class Host(StrictModel):
    id: HostId
    display_name: Annotated[str, Field(min_length=1)] | None = None
    baseline_capabilities: list[HostBaselineCapability] = Field(default_factory=list)
    self_deploy_v2_reconcile_enabled: bool = False
    host_bridge_ingress: list[HostBridgeIngress] = Field(default_factory=list)

    @field_validator("baseline_capabilities")
    @classmethod
    def reject_duplicate_baseline_capabilities(
        cls, value: list[HostBaselineCapability]
    ) -> list[HostBaselineCapability]:
        if len(value) != len(set(value)):
            raise ValueError("duplicate host baseline capability")
        return value

    @field_validator("host_bridge_ingress")
    @classmethod
    def reject_duplicate_host_bridge_ingress(
        cls, value: list[HostBridgeIngress]
    ) -> list[HostBridgeIngress]:
        identities = {(item.service_instance_id, item.protocol, item.port) for item in value}
        if len(value) != len(identities):
            raise ValueError("duplicate host bridge ingress")
        return value


_SENSITIVE_KEY = re.compile(r"(?:^|[^a-z])(value|secret|password|token)(?:$|[^a-z])")


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
    return False


class SecretBinding(StrictModel):
    id: CanonicalId
    slot_id: CanonicalId
    alias: CanonicalId
    delivery: SecretDeliveryForm | None = None
    renderer_binding_id: CanonicalId | None = None


class ProviderAlias(StrictModel):
    id: CanonicalId
    provider: Annotated[str, Field(min_length=1)]
    project: Annotated[str, Field(min_length=1)]
    object_id: Annotated[str, Field(min_length=1)]
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def reject_inline_secrets(cls, value: dict[str, Any]) -> dict[str, Any]:
        if _contains_inline_secret(value):
            raise ValueError("provider alias metadata contains inline secret-like key")
        return value


class DependencyContract(StrictModel):
    id: CanonicalId
    source_service_id: QualifiedRef
    target_service_id: QualifiedRef
    target_endpoint_id: QualifiedRef
    protocol: EndpointProtocol
    port: Port
    required: bool = True
    health_signal_ref: QualifiedRef
    execution_adapter: str | None = "gatus"


class Application(StrictModel):
    id: CanonicalId
    service_instance_ids: Annotated[list[QualifiedRef], Field(min_length=1)]
    required_dependency_edge_ids: list[CanonicalId] = Field(default_factory=list)
    health_signal_refs: list[QualifiedRef] = Field(default_factory=list)


class RendererBindingIdentity(StrictModel):
    id: CanonicalId
    renderer: Annotated[str, Field(min_length=1)]
    binding_ref: Annotated[str, Field(min_length=1)]
    delivery_forms: list[SecretDeliveryForm] = Field(default_factory=list)


class ObservationBackend(StrictModel):
    id: CanonicalId
    kind: BackendKind
    backend_ref: Annotated[str, Field(min_length=1)]


class DatasourceBinding(StrictModel):
    id: CanonicalId
    observation_backend_id: CanonicalId
    datasource_ref: Annotated[str, Field(min_length=1)]
    observed_at: datetime | None = None


class WaiverScope(StrictModel):
    """Stable identity of the exact contract member waived."""

    kind: WaiverScopeKind
    ref: QualifiedRef
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
    signal_id: CanonicalId
    signal_ref: QualifiedRef
    datasource_binding_id: CanonicalId
    requirement: SignalRequirement
    display: SignalDisplay = SignalDisplay.STATUS
    label: str | None = None
    unit: str | None = None
    threshold: Annotated[float, Field(allow_inf_nan=False)] | None = None
    visualization: CanonicalId | None = None
    visualization_class: CanonicalId | None = None
    aggregation: CanonicalId | None = None


class OperationsViewSection(StrictModel):
    id: CanonicalId
    title: Annotated[str, Field(min_length=1)] | None = None
    members: Annotated[list[SignalMembership], Field(min_length=1)]


class OperationsView(StrictModel):
    id: CanonicalId
    purpose: Annotated[str, Field(min_length=1)]
    sections: list[OperationsViewSection]
    kind: Literal["standard", "fleet_convergence", "host_metrics", "profile_metrics"] = "standard"
    freshness_seconds: PositiveSeconds | None = None
    datasource_binding_id: CanonicalId | None = None
    metric_profile_id: CanonicalId | None = None

    @model_validator(mode="after")
    def validate_kind(self) -> OperationsView:
        if self.kind == "standard":
            if (
                not self.sections
                or self.freshness_seconds is not None
                or self.datasource_binding_id is not None
                or self.metric_profile_id is not None
            ):
                raise ValueError("standard view cannot declare fleet convergence fields")
            return self
        if self.kind == "fleet_convergence":
            if (
                self.sections
                or self.freshness_seconds is None
                or self.datasource_binding_id is None
                or self.metric_profile_id is not None
            ):
                raise ValueError("fleet convergence view has an invalid declaration")
            return self
        if (
            self.sections
            or self.freshness_seconds is not None
            or self.datasource_binding_id is None
            or self.metric_profile_id is None
        ):
            raise ValueError(f"{self.kind} view has an invalid declaration")
        return self


class SuiteMember(StrictModel):
    id: CanonicalId
    signal_ref: QualifiedRef
    cadence_seconds: PositiveSeconds
    continuity_seconds: Annotated[StrictInt, Field(ge=0)]
    freshness_seconds: PositiveSeconds
    no_data_policy: FailurePolicy
    error_policy: FailurePolicy


class ReadinessSuite(StrictModel):
    id: CanonicalId
    members: Annotated[list[SuiteMember], Field(min_length=1)]
