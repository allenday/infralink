"""Strict source models for the additive observation v2 component topology."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, StringConstraints, TypeAdapter, field_validator, model_validator

from infralink.observation.models import (
    CanonicalId,
    Endpoint,
    HostId,
    MetricCondition,
    StrictModel,
)

_canonical_id_adapter = TypeAdapter(CanonicalId)
_host_id_adapter = TypeAdapter(HostId)

PrometheusMetricName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-zA-Z_:][a-zA-Z0-9_:]*$"),
]
PrometheusLabelName = Annotated[
    str,
    StringConstraints(pattern=r"^[a-zA-Z_][a-zA-Z0-9_]*$"),
]


class EdgeScope(str, Enum):
    INTRA_SERVICE = "intra-service"
    INTER_SERVICE = "inter-service"


def parse_component_endpoint_ref(value: str) -> tuple[str, str, str, str]:
    """Validate and split a canonical host/service/component/endpoint identity."""
    parts = value.split("/")
    if len(parts) != 4:
        raise ValueError("component endpoint reference must use host/service/component/endpoint")
    host_id, service_id, component_id, endpoint_id = parts
    try:
        return (
            _host_id_adapter.validate_python(host_id),
            _canonical_id_adapter.validate_python(service_id),
            _canonical_id_adapter.validate_python(component_id),
            _canonical_id_adapter.validate_python(endpoint_id),
        )
    except (TypeError, ValueError) as error:
        raise ValueError(
            "component endpoint reference must use host/service/component/endpoint"
        ) from error


class ComponentSlot(StrictModel):
    """A component template owned by a service profile."""

    id: CanonicalId
    endpoints: list[Endpoint] = Field(default_factory=list)
    metrics: list[MetricContract] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_endpoint_ids(self) -> ComponentSlot:
        endpoint_ids = [endpoint.id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("duplicate component endpoint id")
        if any(endpoint.address is not None for endpoint in self.endpoints):
            raise ValueError("component endpoint address must be bound by a service instance")
        metric_ids = [metric.id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("duplicate component metric id")
        endpoint_id_set = set(endpoint_ids)
        for metric in self.metrics:
            if metric.endpoint_id not in endpoint_id_set:
                raise ValueError("component metric references an unknown endpoint")
        return self


class MetricContract(StrictModel):
    """One component-owned application metric and readiness assertion."""

    id: CanonicalId
    endpoint_id: CanonicalId
    path: str
    metric_name: PrometheusMetricName
    unit: CanonicalId
    allowed_labels: list[PrometheusLabelName] = Field(default_factory=list)
    health_query: str | None = None
    condition: MetricCondition | None = None
    readiness_required: bool = False

    @field_validator("path")
    @classmethod
    def require_absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("metric scrape path must be absolute")
        return value

    @model_validator(mode="after")
    def validate_metric_health_contract(self) -> MetricContract:
        if len(self.allowed_labels) != len(set(self.allowed_labels)):
            raise ValueError("duplicate allowed metric label")
        if (self.health_query is None) != (self.condition is None):
            raise ValueError("metric health query and condition must be declared together")
        if self.readiness_required and self.health_query is None:
            raise ValueError("readiness-required metric must declare health query and condition")
        return self


class EndpointBinding(StrictModel):
    """Deployment-specific address for one component endpoint."""

    endpoint_id: CanonicalId
    address: Annotated[str, Field(min_length=1)]


class MetricBinding(StrictModel):
    """Deployment-specific labels for one declared component metric."""

    metric_id: CanonicalId
    labels: dict[PrometheusLabelName, str] = Field(default_factory=dict)


class ComponentInstance(StrictModel):
    """One realized component slot in a service instance."""

    slot_id: CanonicalId
    endpoint_bindings: list[EndpointBinding] = Field(default_factory=list)
    metric_bindings: list[MetricBinding] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_metric_bindings(self) -> ComponentInstance:
        endpoint_ids = [binding.endpoint_id for binding in self.endpoint_bindings]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("duplicate component endpoint binding")
        metric_ids = [binding.metric_id for binding in self.metric_bindings]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("duplicate component metric binding")
        return self


class ServiceProfileV2(StrictModel):
    """A reusable service profile composed from component slots."""

    id: CanonicalId
    components: list[ComponentSlot] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_component_ids(self) -> ServiceProfileV2:
        component_ids = [component.id for component in self.components]
        if len(component_ids) != len(set(component_ids)):
            raise ValueError("duplicate component slot id")
        return self


class ServiceInstanceV2(StrictModel):
    """A host-local instantiation of a v2 service profile."""

    id: CanonicalId
    host_id: HostId
    profile_id: CanonicalId
    components: list[ComponentInstance] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_slot_bindings(self) -> ServiceInstanceV2:
        slot_ids = [component.slot_id for component in self.components]
        if len(slot_ids) != len(set(slot_ids)):
            raise ValueError("duplicate component slot binding")
        return self


class ComponentEdge(StrictModel):
    """An edge whose ownership is derived from its component endpoint identities."""

    id: CanonicalId
    source_endpoint_id: str
    target_endpoint_id: str

    @model_validator(mode="after")
    def validate_endpoint_references(self) -> ComponentEdge:
        parse_component_endpoint_ref(self.source_endpoint_id)
        parse_component_endpoint_ref(self.target_endpoint_id)
        return self

    @property
    def source_owner(self) -> tuple[str, str, str]:
        return parse_component_endpoint_ref(self.source_endpoint_id)[:3]

    @property
    def target_owner(self) -> tuple[str, str, str]:
        return parse_component_endpoint_ref(self.target_endpoint_id)[:3]

    @property
    def scope(self) -> EdgeScope:
        source = parse_component_endpoint_ref(self.source_endpoint_id)
        target = parse_component_endpoint_ref(self.target_endpoint_id)
        return EdgeScope.INTRA_SERVICE if source[:2] == target[:2] else EdgeScope.INTER_SERVICE
