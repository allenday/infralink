"""Parsing boundary for additive observation v2 source documents."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, model_validator

from infralink.observation.models import StrictModel
from infralink.observation.models_v2 import ComponentEdge, ServiceInstanceV2, ServiceProfileV2


class ObservationV2Document(StrictModel):
    """One strict observation v2 source document, without planning or projection."""

    schema_version: Literal["infralink.observation/v2"]
    service_profiles: list[ServiceProfileV2] = Field(default_factory=list)
    service_instances: list[ServiceInstanceV2] = Field(default_factory=list)
    component_edges: list[ComponentEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_component_edge_ids(self) -> ObservationV2Document:
        edge_ids = [edge.id for edge in self.component_edges]
        if len(edge_ids) != len(set(edge_ids)):
            raise ValueError("duplicate component edge id")
        return self


def parse_v2_document(data: dict[str, Any]) -> ObservationV2Document:
    """Parse a v2 document while preserving v1 planner isolation."""
    return ObservationV2Document.model_validate_json(json.dumps(data))
