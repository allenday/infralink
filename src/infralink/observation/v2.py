"""Parsing boundary for additive observation v2 source documents."""

from __future__ import annotations

import json
from typing import Any, Literal

from infralink.observation.models import StrictModel
from infralink.observation.models_v2 import ComponentEdge, ServiceInstanceV2, ServiceProfileV2


class ObservationV2Document(StrictModel):
    """One strict observation v2 source document, without planning or projection."""

    schema_version: Literal["infralink.observation/v2"]
    service_profiles: list[ServiceProfileV2] = []
    service_instances: list[ServiceInstanceV2] = []
    component_edges: list[ComponentEdge] = []


def parse_v2_document(data: dict[str, Any]) -> ObservationV2Document:
    """Parse a v2 document while preserving v1 planner isolation."""
    return ObservationV2Document.model_validate_json(json.dumps(data))
