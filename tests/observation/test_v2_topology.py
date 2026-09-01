"""Declaration-only topology projection coverage for observation v2."""

from __future__ import annotations

import copy
import json

import pytest

from infralink.observation.v2 import ObservationV2Document, V2TopologyValidationError

HOST_ALPHA = "11111111-1111-4111-8111-111111111111"
HOST_BETA = "22222222-2222-4222-8222-222222222222"
HOST_GAMMA = "33333333-3333-4333-8333-333333333333"


def _source() -> dict[str, object]:
    return {
        "schema_version": "infralink.observation/v2",
        "service_profiles": [
            {
                "id": "application",
                "components": [
                    {"id": "api", "endpoints": [{"id": "egress", "protocol": "tcp", "port": 8080}]},
                    {
                        "id": "database",
                        "endpoints": [{"id": "ingress", "protocol": "tcp", "port": 5432}],
                    },
                    {"id": "idle", "endpoints": [{"id": "ready", "protocol": "tcp", "port": 8081}]},
                ],
            },
            {
                "id": "worker",
                "components": [
                    {
                        "id": "worker",
                        "endpoints": [{"id": "input", "protocol": "tcp", "port": 9000}],
                    }
                ],
            },
        ],
        "service_instances": [
            {
                "id": "alpha",
                "host_id": HOST_ALPHA,
                "profile_id": "application",
                "components": [
                    {
                        "slot_id": "api",
                        "endpoint_bindings": [
                            {"endpoint_id": "egress", "address": "198.51.100.10"}
                        ],
                    },
                    {"slot_id": "database"},
                    {"slot_id": "idle"},
                ],
            },
            {
                "id": "beta",
                "host_id": HOST_BETA,
                "profile_id": "worker",
                "components": [{"slot_id": "worker"}],
            },
            {
                "id": "gamma",
                "host_id": HOST_GAMMA,
                "profile_id": "worker",
                "components": [{"slot_id": "worker"}],
            },
        ],
        "component_edges": [
            {
                "id": "api-to-beta",
                "source_endpoint_id": f"{HOST_ALPHA}/alpha/api/egress",
                "target_endpoint_id": f"{HOST_BETA}/beta/worker/input",
            },
            {
                "id": "api-to-database",
                "source_endpoint_id": f"{HOST_ALPHA}/alpha/api/egress",
                "target_endpoint_id": f"{HOST_ALPHA}/alpha/database/ingress",
            },
        ],
    }


def _document() -> ObservationV2Document:
    return ObservationV2Document.model_validate_json(json.dumps(_source()))


def test_projects_a_typed_v2_topology_golden() -> None:
    from infralink.observation.topology import project_v2_topology

    projection = project_v2_topology((_document(),))

    rendered = projection.model_dump(mode="json")
    assert "198.51.100.10" not in json.dumps(rendered)
    assert rendered == {
        "schema_version": "infralink.observation-topology/v2",
        "filter": {"mode": "full", "host_id": None, "service_instance_id": None},
        "nodes": [
            {
                "id": f"{HOST_ALPHA}/alpha/api/egress",
                "owner": {
                    "host_id": HOST_ALPHA,
                    "service_instance_id": "alpha",
                    "component_slot_id": "api",
                },
                "endpoint_id": "egress",
                "protocol": "tcp",
                "port": 8080,
            },
            {
                "id": f"{HOST_ALPHA}/alpha/database/ingress",
                "owner": {
                    "host_id": HOST_ALPHA,
                    "service_instance_id": "alpha",
                    "component_slot_id": "database",
                },
                "endpoint_id": "ingress",
                "protocol": "tcp",
                "port": 5432,
            },
            {
                "id": f"{HOST_ALPHA}/alpha/idle/ready",
                "owner": {
                    "host_id": HOST_ALPHA,
                    "service_instance_id": "alpha",
                    "component_slot_id": "idle",
                },
                "endpoint_id": "ready",
                "protocol": "tcp",
                "port": 8081,
            },
            {
                "id": f"{HOST_BETA}/beta/worker/input",
                "owner": {
                    "host_id": HOST_BETA,
                    "service_instance_id": "beta",
                    "component_slot_id": "worker",
                },
                "endpoint_id": "input",
                "protocol": "tcp",
                "port": 9000,
            },
            {
                "id": f"{HOST_GAMMA}/gamma/worker/input",
                "owner": {
                    "host_id": HOST_GAMMA,
                    "service_instance_id": "gamma",
                    "component_slot_id": "worker",
                },
                "endpoint_id": "input",
                "protocol": "tcp",
                "port": 9000,
            },
        ],
        "edges": [
            {
                "id": "api-to-beta",
                "source_endpoint_id": f"{HOST_ALPHA}/alpha/api/egress",
                "target_endpoint_id": f"{HOST_BETA}/beta/worker/input",
                "source_owner": {
                    "host_id": HOST_ALPHA,
                    "service_instance_id": "alpha",
                    "component_slot_id": "api",
                },
                "target_owner": {
                    "host_id": HOST_BETA,
                    "service_instance_id": "beta",
                    "component_slot_id": "worker",
                },
                "scope": "inter-service",
            },
            {
                "id": "api-to-database",
                "source_endpoint_id": f"{HOST_ALPHA}/alpha/api/egress",
                "target_endpoint_id": f"{HOST_ALPHA}/alpha/database/ingress",
                "source_owner": {
                    "host_id": HOST_ALPHA,
                    "service_instance_id": "alpha",
                    "component_slot_id": "api",
                },
                "target_owner": {
                    "host_id": HOST_ALPHA,
                    "service_instance_id": "alpha",
                    "component_slot_id": "database",
                },
                "scope": "intra-service",
            },
        ],
    }


def test_v2_topology_projection_is_deterministic_for_declaration_order() -> None:
    from infralink.observation.topology import project_v2_topology

    reordered = _source()
    for collection in ("service_profiles", "service_instances", "component_edges"):
        values = reordered[collection]
        assert isinstance(values, list)
        values.reverse()

    assert project_v2_topology((_document(),)) == project_v2_topology(
        (ObservationV2Document.model_validate_json(json.dumps(reordered)),)
    )


def test_v2_topology_projection_includes_direct_neighbours_for_host_and_service_filters() -> None:
    from infralink.observation.topology import project_v2_topology

    full = project_v2_topology((_document(),))
    host = project_v2_topology((_document(),), focal_host_id=HOST_ALPHA)
    service = project_v2_topology((_document(),), focal_service_instance_id="alpha")

    assert {node.id for node in full.nodes} == {
        f"{HOST_ALPHA}/alpha/api/egress",
        f"{HOST_ALPHA}/alpha/database/ingress",
        f"{HOST_ALPHA}/alpha/idle/ready",
        f"{HOST_BETA}/beta/worker/input",
        f"{HOST_GAMMA}/gamma/worker/input",
    }
    assert {node.id for node in host.nodes} == {node.id for node in full.nodes} - {
        f"{HOST_GAMMA}/gamma/worker/input"
    }
    assert {node.id for node in service.nodes} == {
        f"{HOST_ALPHA}/alpha/api/egress",
        f"{HOST_ALPHA}/alpha/database/ingress",
        f"{HOST_ALPHA}/alpha/idle/ready",
        f"{HOST_BETA}/beta/worker/input",
    }
    assert [edge.id for edge in host.edges] == ["api-to-beta", "api-to-database"]
    assert [edge.id for edge in service.edges] == ["api-to-beta", "api-to-database"]


def test_v2_topology_projection_uses_the_resolver_for_invalid_declarations() -> None:
    from infralink.observation.topology import project_v2_topology

    invalid = _source()
    edges = invalid["component_edges"]
    assert isinstance(edges, list)
    edge = copy.deepcopy(edges[0])
    assert isinstance(edge, dict)
    edge["target_endpoint_id"] = f"{HOST_BETA}/beta/worker/missing"
    invalid["component_edges"] = [edge]

    with pytest.raises(V2TopologyValidationError, match="unknown component endpoint"):
        project_v2_topology((ObservationV2Document.model_validate_json(json.dumps(invalid)),))
