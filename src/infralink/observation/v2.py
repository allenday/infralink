"""Parsing boundary for additive observation v2 source documents."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Literal

from pydantic import Field, model_validator

from infralink.observation.models import Endpoint, EndpointProtocol, ProviderAlias, StrictModel
from infralink.observation.models_v2 import (
    ComponentEdge,
    ExternalServiceContract,
    MetricContract,
    ResourceKind,
    SecretReference,
    ServiceInstanceV2,
    ServiceProfileV2,
)


class ObservationV2Document(StrictModel):
    """One strict observation v2 source document, without planning or projection."""

    schema_version: Literal["infralink.observation/v2"]
    service_profiles: list[ServiceProfileV2] = Field(default_factory=list)
    service_instances: list[ServiceInstanceV2] = Field(default_factory=list)
    component_edges: list[ComponentEdge] = Field(default_factory=list)
    external_service_contracts: list[ExternalServiceContract] = Field(default_factory=list)
    provider_aliases: list[ProviderAlias] = Field(default_factory=list)
    secret_references: list[SecretReference] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_topology_identities(self) -> ObservationV2Document:
        profile_ids = [profile.id for profile in self.service_profiles]
        if len(profile_ids) != len(set(profile_ids)):
            raise ValueError("duplicate service profile id")

        instance_ids = [(instance.host_id, instance.id) for instance in self.service_instances]
        if len(instance_ids) != len(set(instance_ids)):
            raise ValueError("duplicate service instance id on host")

        edge_ids = [edge.id for edge in self.component_edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("duplicate component edge id")
        external_service_ids = [contract.id for contract in self.external_service_contracts]
        if len(external_service_ids) != len(set(external_service_ids)):
            raise ValueError("duplicate external service contract id")
        secret_reference_ids = [reference.id for reference in self.secret_references]
        if len(secret_reference_ids) != len(set(secret_reference_ids)):
            raise ValueError("duplicate secret reference id")

        return self


class V2TopologyValidationError(ValueError):
    """One stable, source-addressable cross-document topology failure."""

    def __init__(self, code: str, edge_id: str, message: str) -> None:
        self.code = code
        self.edge_id = edge_id
        super().__init__(message)


class V2InstanceTopologyValidationError(ValueError):
    """One stable, source-addressable v2 instance resolution failure."""

    def __init__(
        self,
        code: str,
        host_id: str,
        instance_id: str,
        message: str,
        *,
        slot_id: str | None = None,
    ) -> None:
        self.code = code
        self.host_id = host_id
        self.instance_id = instance_id
        self.slot_id = slot_id
        super().__init__(message)


class V2MetricValidationError(ValueError):
    """One stable, source-addressable v2 metric binding failure."""

    def __init__(
        self,
        code: str,
        host_id: str,
        instance_id: str,
        component_id: str,
        metric_id: str,
        location_kind: Literal["component", "endpoint-binding", "metric-binding", "metric-id"],
        message: str,
    ) -> None:
        self.code = code
        self.host_id = host_id
        self.instance_id = instance_id
        self.component_id = component_id
        self.metric_id = metric_id
        self.location_kind = location_kind
        super().__init__(message)


class V2ResourceValidationError(ValueError):
    """One stable, source-addressable v2 component resource failure."""

    def __init__(
        self,
        code: str,
        host_id: str,
        instance_id: str,
        component_id: str,
        resource_id: str,
        location_kind: Literal["resource-id", "resource-bindings", "reference"],
        message: str,
    ) -> None:
        self.code, self.host_id, self.instance_id = code, host_id, instance_id
        self.component_id, self.resource_id, self.location_kind = (
            component_id,
            resource_id,
            location_kind,
        )
        super().__init__(message)


class PrometheusMetricProjection(StrictModel):
    endpoint_id: str
    protocol: EndpointProtocol
    address: str | None = None
    port: int
    path: str
    labels: dict[str, str]


class GatusMetricProjection(StrictModel):
    endpoint_id: str
    protocol: EndpointProtocol
    address: str | None = None
    port: int
    path: str


class GrafanaMetricProjection(StrictModel):
    metric_name: str
    unit: str
    labels: dict[str, str]


class DoctorMetricProjection(StrictModel):
    required: bool
    query: str | None = None
    operator: str | None = None
    threshold: float | None = None


class PlannedMetricContract(StrictModel):
    """One normalized metric identity with adapter-specific projections."""

    id: str
    prometheus: PrometheusMetricProjection
    gatus: GatusMetricProjection
    grafana: GrafanaMetricProjection
    doctor: DoctorMetricProjection


def validate_v2_documents(documents: Iterable[ObservationV2Document]) -> None:
    """Resolve v2 topology across the complete source set."""

    document_list = tuple(documents)
    profiles = {
        profile.id: profile for document in document_list for profile in document.service_profiles
    }
    external_service_contracts = {
        contract.id
        for document in document_list
        for contract in document.external_service_contracts
    }
    provider_aliases = {
        alias.id for document in document_list for alias in document.provider_aliases
    }
    secret_references = {
        reference.id: reference
        for document in document_list
        for reference in document.secret_references
    }
    endpoint_refs: dict[str, Endpoint] = {}
    edges = [edge for document in document_list for edge in document.component_edges]
    for document in document_list:
        for instance in document.service_instances:
            profile = profiles.get(instance.profile_id)
            if profile is None:
                raise V2InstanceTopologyValidationError(
                    "service-instance-unknown-profile",
                    instance.host_id,
                    instance.id,
                    "unknown service profile",
                )
            slots = {slot.id: slot for slot in profile.components}
            for component in instance.components:
                slot = slots.get(component.slot_id)
                if slot is None:
                    raise V2InstanceTopologyValidationError(
                        "service-instance-unknown-component-slot",
                        instance.host_id,
                        instance.id,
                        "unknown component slot",
                        slot_id=component.slot_id,
                    )
                metric_contracts = {metric.id: metric for metric in slot.metrics}
                resource_slots = {resource.id: resource for resource in slot.resource_slots}
                resource_bindings = {
                    binding.resource_id: binding for binding in component.resource_bindings
                }
                for resource_id in resource_bindings:
                    if resource_id not in resource_slots:
                        raise V2ResourceValidationError(
                            "component-resource-binding-unknown-slot",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            resource_id,
                            "resource-id",
                            "component resource binding references an unknown resource slot",
                        )
                for resource in slot.resource_slots:
                    if resource.required and resource.id not in resource_bindings:
                        raise V2ResourceValidationError(
                            "component-resource-required-unbound",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            resource.id,
                            "resource-bindings",
                            "required component resource slot is unbound",
                        )
                    if (
                        resource.kind is ResourceKind.EXTERNAL_SERVICE
                        and resource.contract_ref not in external_service_contracts
                    ):
                        raise V2ResourceValidationError(
                            "external-service-resource-unknown-contract",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            resource.id,
                            "resource-id",
                            "external-service resource slot references an unknown contract",
                        )
                    if resource.kind is ResourceKind.SECRET and resource.id in resource_bindings:
                        reference = resource_bindings[resource.id].reference
                        secret_reference = secret_references.get(reference)
                        if (
                            secret_reference is None
                            or secret_reference.provider_alias_id not in provider_aliases
                        ):
                            raise V2ResourceValidationError(
                                "secret-resource-binding-invalid-reference",
                                instance.host_id,
                                instance.id,
                                component.slot_id,
                                resource.id,
                                "reference",
                                "secret resource binding must select a declared value-free secret reference",
                            )
                    if resource.kind is ResourceKind.EXTERNAL_SERVICE and (
                        resource.id in resource_bindings
                        and resource_bindings[resource.id].reference != resource.contract_ref
                    ):
                        raise V2ResourceValidationError(
                            "external-service-resource-binding-mismatch",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            resource.id,
                            "reference",
                            "external-service resource binding must match contract_ref",
                        )
                endpoint_ids = {endpoint.id for endpoint in slot.endpoints}
                endpoint_bindings = {
                    binding.endpoint_id: binding for binding in component.endpoint_bindings
                }
                for endpoint_id in endpoint_bindings:
                    if endpoint_id not in endpoint_ids:
                        raise V2MetricValidationError(
                            "component-endpoint-binding-unknown-endpoint",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            endpoint_id,
                            "endpoint-binding",
                            "component endpoint binding references an unknown endpoint",
                        )
                for binding in component.metric_bindings:
                    metric_contract = metric_contracts.get(binding.metric_id)
                    if metric_contract is None:
                        raise V2MetricValidationError(
                            "component-metric-binding-unknown-contract",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            binding.metric_id,
                            "metric-id",
                            "component metric binding references an unknown metric contract",
                        )
                    if set(binding.labels) - set(metric_contract.allowed_labels):
                        raise V2MetricValidationError(
                            "component-metric-binding-label-not-allowed",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            binding.metric_id,
                            "metric-binding",
                            "metric label is not allowed by component contract",
                        )
                for metric in slot.metrics:
                    if metric.endpoint_id not in endpoint_bindings:
                        raise V2MetricValidationError(
                            "component-metric-source-endpoint-unbound",
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            metric.id,
                            "component",
                            "component metric source endpoint has no instance address binding",
                        )
                for endpoint in slot.endpoints:
                    endpoint_refs[
                        f"{instance.host_id}/{instance.id}/{component.slot_id}/{endpoint.id}"
                    ] = endpoint
            bound_slot_ids = {component.slot_id for component in instance.components}
            missing_slot_ids = sorted(set(slots) - bound_slot_ids)
            if missing_slot_ids:
                raise V2InstanceTopologyValidationError(
                    "service-instance-missing-component-slot",
                    instance.host_id,
                    instance.id,
                    "service instance is missing a required component slot binding",
                    slot_id=missing_slot_ids[0],
                )

    edge_semantics = [(edge.source_endpoint_id, edge.target_endpoint_id) for edge in edges]
    if len(edge_semantics) != len(set(edge_semantics)):
        duplicate_index = next(
            index
            for index, identity in enumerate(edge_semantics)
            if identity in edge_semantics[:index]
        )
        raise V2TopologyValidationError(
            "duplicate-component-edge-semantics",
            edges[duplicate_index].id,
            "duplicate component edge semantics",
        )
    for edge in edges:
        if edge.source_endpoint_id not in endpoint_refs:
            raise V2TopologyValidationError(
                "component-edge-unknown-endpoint",
                edge.id,
                "unknown component endpoint",
            )
        if edge.target_endpoint_id not in endpoint_refs:
            raise V2TopologyValidationError(
                "component-edge-unknown-endpoint",
                edge.id,
                "unknown component endpoint",
            )
        if (
            endpoint_refs[edge.source_endpoint_id].protocol
            != endpoint_refs[edge.target_endpoint_id].protocol
        ):
            raise V2TopologyValidationError(
                "component-edge-incompatible-protocol",
                edge.id,
                "incompatible component endpoint protocols",
            )


def parse_v2_document(data: dict[str, Any]) -> ObservationV2Document:
    """Parse a v2 document while preserving v1 planner isolation."""
    parsed = ObservationV2Document.model_validate_json(json.dumps(data))
    validate_v2_documents((parsed,))
    return parsed


def plan_v2_metric_contracts(
    documents: Iterable[ObservationV2Document],
) -> tuple[PlannedMetricContract, ...]:
    """Project component metric contracts once for all observation adapters."""

    document_list = tuple(documents)
    validate_v2_documents(document_list)
    profiles = {
        profile.id: profile for document in document_list for profile in document.service_profiles
    }
    projections: list[PlannedMetricContract] = []
    for document in document_list:
        for instance in document.service_instances:
            profile = profiles[instance.profile_id]
            slots = {slot.id: slot for slot in profile.components}
            for component in instance.components:
                slot = slots[component.slot_id]
                bindings = {binding.metric_id: binding for binding in component.metric_bindings}
                endpoints = {endpoint.id: endpoint for endpoint in slot.endpoints}
                endpoint_bindings = {
                    binding.endpoint_id: binding for binding in component.endpoint_bindings
                }
                for metric in slot.metrics:
                    binding = bindings.get(metric.id)
                    labels = {} if binding is None else dict(binding.labels)
                    endpoint_id = (
                        f"{instance.host_id}/{instance.id}/{component.slot_id}/{metric.endpoint_id}"
                    )
                    projections.append(
                        _project_metric_contract(
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            metric,
                            endpoint=endpoints[metric.endpoint_id],
                            endpoint_address=endpoint_bindings[metric.endpoint_id].address,
                            endpoint_id=endpoint_id,
                            labels=labels,
                        )
                    )
    return tuple(sorted(projections, key=lambda projection: projection.id))


def _project_metric_contract(
    host_id: str,
    service_id: str,
    component_id: str,
    metric: MetricContract,
    endpoint: Endpoint,
    endpoint_address: str,
    endpoint_id: str,
    labels: dict[str, str],
) -> PlannedMetricContract:
    return PlannedMetricContract(
        id=f"{host_id}/{service_id}/{component_id}/{metric.id}",
        prometheus=PrometheusMetricProjection(
            endpoint_id=endpoint_id,
            protocol=endpoint.protocol,
            address=endpoint_address,
            port=endpoint.port,
            path=metric.path,
            labels=labels,
        ),
        gatus=GatusMetricProjection(
            endpoint_id=endpoint_id,
            protocol=endpoint.protocol,
            address=endpoint_address,
            port=endpoint.port,
            path=metric.path,
        ),
        grafana=GrafanaMetricProjection(
            metric_name=metric.metric_name,
            unit=metric.unit,
            labels=labels,
        ),
        doctor=DoctorMetricProjection(
            required=metric.readiness_required,
            query=metric.health_query,
            operator=None if metric.condition is None else metric.condition.operator.value,
            threshold=None if metric.condition is None else metric.condition.threshold,
        ),
    )
