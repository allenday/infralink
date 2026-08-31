"""Parsing boundary for additive observation v2 source documents."""

from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any, Literal, cast

from pydantic import Field, model_validator

from infralink.observation.models import Endpoint, EndpointProtocol, ProviderAlias, StrictModel
from infralink.observation.models_v2 import (
    ArtifactBinding,
    ArtifactKind,
    ArtifactSlot,
    ArtifactSource,
    ComponentEdge,
    ConfigurationFieldKind,
    ConfigurationRecordValue,
    ConfigurationSlot,
    ConfigurationValue,
    ConfigurationValueKind,
    ConnectionCardinality,
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


class V2ConfigurationValidationError(ValueError):
    """One stable, source-addressable v2 configuration binding failure."""

    def __init__(
        self,
        code: str,
        host_id: str,
        instance_id: str,
        configuration_slot_id: str,
        message: str,
    ) -> None:
        self.code, self.host_id, self.instance_id = code, host_id, instance_id
        self.configuration_slot_id = configuration_slot_id
        super().__init__(message)


class V2ArtifactValidationError(ValueError):
    """One stable, source-addressable v2 artifact binding failure."""

    def __init__(
        self,
        code: str,
        host_id: str,
        instance_id: str,
        artifact_slot_id: str,
        message: str,
    ) -> None:
        self.code, self.host_id, self.instance_id = code, host_id, instance_id
        self.artifact_slot_id = artifact_slot_id
        super().__init__(message)


def _validate_configuration_field_value(kind: ConfigurationFieldKind, value: object) -> bool:
    if kind is ConfigurationFieldKind.STRING:
        return isinstance(value, str)
    if kind is ConfigurationFieldKind.INTEGER:
        return isinstance(value, int) and not isinstance(value, bool)
    if kind is ConfigurationFieldKind.BOOLEAN:
        return isinstance(value, bool)
    if kind is ConfigurationFieldKind.STRING_LIST:
        return isinstance(value, list) and all(isinstance(item, str) for item in value)
    return isinstance(value, dict) and all(
        isinstance(key, str)
        and isinstance(hostnames, list)
        and all(isinstance(hostname, str) for hostname in hostnames)
        for key, hostnames in value.items()
    )


def _validate_configuration_record(slot: ConfigurationSlot, value: object) -> bool:
    if not isinstance(value, dict):
        return False
    fields = {field.id: field for field in slot.fields}
    unknown_fields = set(value) - set(fields)
    if unknown_fields:
        raise ValueError("record configuration value has an unknown field")
    for field in slot.fields:
        if field.required and field.id not in value:
            raise ValueError("record configuration value is missing a required field")
        if field.id in value and not _validate_configuration_field_value(
            field.kind, value[field.id]
        ):
            raise ValueError("record configuration value has an incompatible field type")
    return True


def _validate_configuration_value(slot: ConfigurationSlot, value: ConfigurationValue) -> None:
    """Reject non-secret values that do not match a profile's finite slot shape."""

    if slot.kind is ConfigurationValueKind.STRING:
        valid = isinstance(value, str)
    elif slot.kind is ConfigurationValueKind.INTEGER:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif slot.kind is ConfigurationValueKind.BOOLEAN:
        valid = isinstance(value, bool)
    elif slot.kind is ConfigurationValueKind.STRING_LIST:
        valid = isinstance(value, list) and all(isinstance(item, str) for item in value)
    elif slot.kind is ConfigurationValueKind.STRING_LIST_MAP:
        valid = isinstance(value, dict) and all(
            isinstance(key, str)
            and isinstance(hostnames, list)
            and all(isinstance(hostname, str) for hostname in hostnames)
            for key, hostnames in value.items()
        )
    elif slot.kind is ConfigurationValueKind.RECORD:
        valid = _validate_configuration_record(slot, value)
    else:
        valid = isinstance(value, list) and all(
            _validate_configuration_record(slot, item) for item in value
        )
        if valid:
            assert slot.identity_field is not None
            records = cast(list[ConfigurationRecordValue], value)
            identities: list[str | int | bool] = []
            for record in records:
                identity = record[slot.identity_field]
                if not isinstance(identity, (str, int, bool)):
                    raise ValueError("record-list configuration identity has an incompatible type")
                identities.append(identity)
            if len(identities) != len(set(identities)):
                raise ValueError("record-list configuration value has a duplicate identity")
    if not valid:
        raise ValueError(f"configuration value must match declared {slot.kind.value} type")


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


class PlannedConfigurationBinding(StrictModel):
    """One generic renderer input resolved from a profile configuration contract."""

    host_id: str
    service_instance_id: str
    profile_id: str
    component_id: str | None
    slot_id: str
    slot: ConfigurationSlot
    value: ConfigurationValue | None
    edge_refs: list[str] = Field(default_factory=list)
    targets: list[Endpoint] = Field(default_factory=list)


class PlannedArtifactBinding(StrictModel):
    """One generic materializer input resolved from a profile artifact contract."""

    host_id: str
    service_instance_id: str
    profile_id: str
    component_id: str | None
    slot_id: str
    slot: ArtifactSlot
    sources: list[ArtifactSource]


def _validate_artifact_binding(slot: ArtifactSlot, binding: ArtifactBinding) -> None:
    if slot.kind is ArtifactKind.FILE:
        if len(binding.sources) != 1:
            raise ValueError("file artifact binding requires exactly one source")
        if binding.sources[0].relative_target is not None:
            raise ValueError("file artifact source must not declare relative_target")
        return
    if any(source.relative_target is None for source in binding.sources):
        raise ValueError("tree artifact sources require relative_target")
    targets = [source.relative_target for source in binding.sources]
    if len(targets) != len(set(targets)):
        raise ValueError("tree artifact binding has duplicate relative_target")


def validate_v2_documents(documents: Iterable[ObservationV2Document]) -> dict[str, Endpoint]:
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
    instances_by_identity = {
        (instance.host_id, instance.id): instance
        for document in document_list
        for instance in document.service_instances
    }
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
            configuration_slots = {slot.id: slot for slot in profile.configuration_slots}
            configuration_bindings = {
                binding.slot_id: binding for binding in instance.configuration_bindings
            }
            for configuration_slot_id, configuration_binding in configuration_bindings.items():
                configuration_slot = configuration_slots.get(configuration_slot_id)
                if configuration_slot is None:
                    raise V2ConfigurationValidationError(
                        "service-instance-unknown-configuration-slot",
                        instance.host_id,
                        instance.id,
                        configuration_slot_id,
                        "configuration binding references an unknown configuration slot",
                    )
                if configuration_slot.kind is ConfigurationValueKind.CONNECTION:
                    if configuration_binding.edge_refs is not None:
                        continue
                    raise V2ConfigurationValidationError(
                        "service-instance-connection-binding-invalid-source",
                        instance.host_id,
                        instance.id,
                        configuration_slot_id,
                        "connection configuration slot requires edge_refs",
                    )
                if configuration_binding.value is None:
                    raise V2ConfigurationValidationError(
                        "service-instance-configuration-binding-invalid-source",
                        instance.host_id,
                        instance.id,
                        configuration_slot_id,
                        "static configuration slot requires value",
                    )
                try:
                    _validate_configuration_value(configuration_slot, configuration_binding.value)
                except ValueError as error:
                    raise V2ConfigurationValidationError(
                        "service-instance-configuration-value-invalid",
                        instance.host_id,
                        instance.id,
                        configuration_slot_id,
                        str(error),
                    ) from error
            for configuration_slot in profile.configuration_slots:
                if (
                    configuration_slot.required
                    and configuration_slot.id not in configuration_bindings
                ):
                    raise V2ConfigurationValidationError(
                        "service-instance-required-configuration-unbound",
                        instance.host_id,
                        instance.id,
                        configuration_slot.id,
                        "required configuration slot is unbound",
                    )
            artifact_slots = {slot.id: slot for slot in profile.artifact_slots}
            artifact_bindings = {binding.slot_id: binding for binding in instance.artifact_bindings}
            for artifact_slot_id, artifact_binding in artifact_bindings.items():
                artifact_slot = artifact_slots.get(artifact_slot_id)
                if artifact_slot is None:
                    raise V2ArtifactValidationError(
                        "service-instance-unknown-artifact-slot",
                        instance.host_id,
                        instance.id,
                        artifact_slot_id,
                        "artifact binding references an unknown artifact slot",
                    )
                try:
                    _validate_artifact_binding(artifact_slot, artifact_binding)
                except ValueError as error:
                    raise V2ArtifactValidationError(
                        "service-instance-artifact-value-invalid",
                        instance.host_id,
                        instance.id,
                        artifact_slot_id,
                        str(error),
                    ) from error
            for artifact_slot in profile.artifact_slots:
                if artifact_slot.required and artifact_slot.id not in artifact_bindings:
                    raise V2ArtifactValidationError(
                        "service-instance-required-artifact-unbound",
                        instance.host_id,
                        instance.id,
                        artifact_slot.id,
                        "required artifact slot is unbound",
                    )
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
                endpoint_overrides = {
                    override.endpoint_id: override for override in component.endpoint_overrides
                }
                for endpoint_id in endpoint_overrides:
                    if endpoint_id not in endpoint_ids:
                        raise V2InstanceTopologyValidationError(
                            "service-instance-unknown-component-endpoint",
                            instance.host_id,
                            instance.id,
                            "unknown component endpoint",
                            slot_id=component.slot_id,
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
                    override = endpoint_overrides.get(metric.endpoint_id)
                    if metric.endpoint_id not in endpoint_bindings and (
                        override is None or override.address is None
                    ):
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
                    override = endpoint_overrides.get(endpoint.id)
                    endpoint_binding = endpoint_bindings.get(endpoint.id)
                    resolved_endpoint = endpoint.model_copy(
                        update={
                            "address": override.address
                            if override is not None and override.address is not None
                            else endpoint_binding.canonical_address
                            if endpoint_binding is not None
                            else endpoint.address,
                            "port": override.port
                            if override is not None and override.port is not None
                            else endpoint.port,
                            "exposure": override.exposure
                            if override is not None and override.exposure is not None
                            else endpoint.exposure,
                        }
                    )
                    endpoint_refs[
                        f"{instance.host_id}/{instance.id}/{component.slot_id}/{endpoint.id}"
                    ] = resolved_endpoint
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
    edges_by_id = {edge.id: edge for edge in edges}
    if len(edges_by_id) != len(edges):
        duplicate_edge_id = next(
            edge.id for edge in edges if sum(item.id == edge.id for item in edges) > 1
        )
        raise V2TopologyValidationError(
            "duplicate-component-edge-id",
            duplicate_edge_id,
            "duplicate component edge id",
        )
    for document in document_list:
        for instance in document.service_instances:
            profile = profiles[instance.profile_id]
            configuration_slots = {slot.id: slot for slot in profile.configuration_slots}
            for configuration_binding in instance.configuration_bindings:
                configuration_slot = configuration_slots[configuration_binding.slot_id]
                if configuration_slot.kind is not ConfigurationValueKind.CONNECTION:
                    continue
                assert configuration_slot.component_id is not None
                assert configuration_slot.protocol is not None
                assert configuration_slot.cardinality is not None
                assert configuration_binding.edge_refs is not None
                if (
                    configuration_slot.cardinality is ConnectionCardinality.ONE
                    and len(configuration_binding.edge_refs) != 1
                ):
                    raise V2ConfigurationValidationError(
                        "service-instance-connection-cardinality-invalid",
                        instance.host_id,
                        instance.id,
                        configuration_slot.id,
                        "one connection configuration slot requires exactly one edge reference",
                    )
                for edge_ref in configuration_binding.edge_refs:
                    connection_edge = edges_by_id.get(edge_ref)
                    if connection_edge is None:
                        raise V2ConfigurationValidationError(
                            "service-instance-connection-edge-unknown",
                            instance.host_id,
                            instance.id,
                            configuration_slot.id,
                            "connection configuration binding references an unknown component edge",
                        )
                    source_component_id = connection_edge.source_owner[2]
                    if source_component_id != configuration_slot.component_id:
                        raise V2ConfigurationValidationError(
                            "service-instance-connection-source-component-mismatch",
                            instance.host_id,
                            instance.id,
                            configuration_slot.id,
                            "connection edge source component does not match configuration slot owner",
                        )
                    source_endpoint = endpoint_refs[connection_edge.source_endpoint_id]
                    target_endpoint = endpoint_refs[connection_edge.target_endpoint_id]
                    if (
                        source_endpoint.protocol != configuration_slot.protocol
                        or target_endpoint.protocol != configuration_slot.protocol
                    ):
                        raise V2ConfigurationValidationError(
                            "service-instance-connection-protocol-mismatch",
                            instance.host_id,
                            instance.id,
                            configuration_slot.id,
                            "connection edge protocol does not match configuration slot protocol",
                        )
                    if configuration_slot.target_profile_id is not None:
                        target_owner = connection_edge.target_owner
                        target_instance = instances_by_identity[target_owner[:2]]
                        if target_instance.profile_id != configuration_slot.target_profile_id:
                            raise V2ConfigurationValidationError(
                                "service-instance-connection-target-profile-mismatch",
                                instance.host_id,
                                instance.id,
                                configuration_slot.id,
                                "connection edge target profile does not match configuration slot contract",
                            )
    return endpoint_refs


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
    endpoint_refs = validate_v2_documents(document_list)
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
                for metric in slot.metrics:
                    binding = bindings.get(metric.id)
                    labels = {} if binding is None else dict(binding.labels)
                    endpoint_id = (
                        f"{instance.host_id}/{instance.id}/{component.slot_id}/{metric.endpoint_id}"
                    )
                    endpoint = endpoint_refs[endpoint_id]
                    projections.append(
                        _project_metric_contract(
                            instance.host_id,
                            instance.id,
                            component.slot_id,
                            metric,
                            endpoint=endpoint,
                            endpoint_address=endpoint.address,
                            endpoint_id=endpoint_id,
                            labels=labels,
                        )
                    )
    return tuple(sorted(projections, key=lambda projection: projection.id))


def plan_v2_configuration_bindings(
    documents: Iterable[ObservationV2Document],
) -> tuple[PlannedConfigurationBinding, ...]:
    """Resolve declared non-secret configuration for generic renderers and materializers."""

    document_list = tuple(documents)
    endpoint_refs = validate_v2_documents(document_list)
    profiles = {
        profile.id: profile for document in document_list for profile in document.service_profiles
    }
    resolved: list[PlannedConfigurationBinding] = []
    edges_by_id = {edge.id: edge for document in document_list for edge in document.component_edges}
    for document in document_list:
        for instance in document.service_instances:
            profile = profiles[instance.profile_id]
            slots = {slot.id: slot for slot in profile.configuration_slots}
            for binding in instance.configuration_bindings:
                slot = slots[binding.slot_id]
                edge_refs = binding.edge_refs or []
                targets = (
                    [
                        endpoint_refs[edges_by_id[edge_ref].target_endpoint_id]
                        for edge_ref in edge_refs
                    ]
                    if slot.kind is ConfigurationValueKind.CONNECTION
                    else []
                )
                resolved.append(
                    PlannedConfigurationBinding(
                        host_id=instance.host_id,
                        service_instance_id=instance.id,
                        profile_id=profile.id,
                        component_id=slot.component_id,
                        slot_id=slot.id,
                        slot=slot,
                        value=binding.value,
                        edge_refs=edge_refs,
                        targets=targets,
                    )
                )
    return tuple(
        sorted(
            resolved,
            key=lambda binding: (
                binding.host_id,
                binding.service_instance_id,
                binding.component_id or "",
                binding.slot_id,
            ),
        )
    )


def plan_v2_artifact_bindings(
    documents: Iterable[ObservationV2Document],
) -> tuple[PlannedArtifactBinding, ...]:
    """Resolve integrity-bound artifact sources for generic materializers."""

    document_list = tuple(documents)
    validate_v2_documents(document_list)
    profiles = {
        profile.id: profile for document in document_list for profile in document.service_profiles
    }
    resolved: list[PlannedArtifactBinding] = []
    for document in document_list:
        for instance in document.service_instances:
            profile = profiles[instance.profile_id]
            slots = {slot.id: slot for slot in profile.artifact_slots}
            for binding in instance.artifact_bindings:
                slot = slots[binding.slot_id]
                resolved.append(
                    PlannedArtifactBinding(
                        host_id=instance.host_id,
                        service_instance_id=instance.id,
                        profile_id=profile.id,
                        component_id=slot.component_id,
                        slot_id=slot.id,
                        slot=slot,
                        sources=binding.sources,
                    )
                )
    return tuple(
        sorted(
            resolved,
            key=lambda binding: (
                binding.host_id,
                binding.service_instance_id,
                binding.component_id or "",
                binding.slot_id,
            ),
        )
    )


def _project_metric_contract(
    host_id: str,
    service_id: str,
    component_id: str,
    metric: MetricContract,
    endpoint: Endpoint,
    endpoint_address: str | None,
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
