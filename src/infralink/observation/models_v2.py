"""Strict source models for the additive observation v2 component topology."""

from __future__ import annotations

from enum import Enum

from pydantic import Field, TypeAdapter, model_validator

from infralink.observation.models import CanonicalId, Endpoint, HostId, StrictModel

_canonical_id_adapter = TypeAdapter(CanonicalId)
_host_id_adapter = TypeAdapter(HostId)


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
    endpoints: list[Endpoint] = Field(min_length=1)

    @model_validator(mode="after")
    def reject_duplicate_endpoint_ids(self) -> ComponentSlot:
        endpoint_ids = [endpoint.id for endpoint in self.endpoints]
        if len(endpoint_ids) != len(set(endpoint_ids)):
            raise ValueError("duplicate component endpoint id")
        return self


class ComponentInstance(StrictModel):
    """One realized component slot in a service instance."""

    slot_id: CanonicalId


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
