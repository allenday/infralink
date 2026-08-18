"""Parsing boundary for additive observation v2 source documents."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from infralink.observation.models import Endpoint, StrictModel
from infralink.observation.models_v2 import ComponentEdge, ServiceInstanceV2, ServiceProfileV2


class ObservationV2Document(StrictModel):
    """One strict observation v2 source document, without planning or projection."""

    schema_version: Literal["infralink.observation/v2"]
    service_profiles: list[ServiceProfileV2] = Field(default_factory=list)
    service_instances: list[ServiceInstanceV2] = Field(default_factory=list)
    component_edges: list[ComponentEdge] = Field(default_factory=list)

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

        profiles = {profile.id: profile for profile in self.service_profiles}
        endpoint_refs: dict[str, Endpoint] = {}
        for instance in self.service_instances:
            profile = profiles.get(instance.profile_id)
            if profile is None:
                raise ValueError("unknown service profile")
            slots = {slot.id: slot for slot in profile.components}
            for component in instance.components:
                slot = slots.get(component.slot_id)
                if slot is None:
                    raise ValueError("unknown component slot")
                for endpoint in slot.endpoints:
                    endpoint_refs[
                        f"{instance.host_id}/{instance.id}/{component.slot_id}/{endpoint.id}"
                    ] = endpoint

        edge_semantics = [
            (edge.source_endpoint_id, edge.target_endpoint_id) for edge in self.component_edges
        ]
        if len(edge_semantics) != len(set(edge_semantics)):
            raise ValueError("duplicate component edge semantics")
        for edge in self.component_edges:
            if edge.source_endpoint_id not in endpoint_refs:
                raise ValueError("unknown component endpoint")
            if edge.target_endpoint_id not in endpoint_refs:
                raise ValueError("unknown component endpoint")
            if (
                endpoint_refs[edge.source_endpoint_id].protocol
                != endpoint_refs[edge.target_endpoint_id].protocol
            ):
                raise ValueError("incompatible component endpoint protocols")
        return self


def parse_v2_document(data: dict[str, Any]) -> ObservationV2Document:
    """Parse a v2 document while preserving v1 planner isolation."""
    return ObservationV2Document.model_validate_json(json.dumps(data))
