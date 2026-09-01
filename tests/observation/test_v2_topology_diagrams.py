"""Golden coverage for declaration-only observation v2 topology diagrams."""

from __future__ import annotations

import re

from infralink.observation.models import EndpointProtocol
from infralink.observation.models_v2 import EdgeScope
from infralink.observation.topology import (
    V2TopologyEdge,
    V2TopologyNode,
    V2TopologyOwner,
    V2TopologyProjection,
)

HOST_ALPHA = "11111111-1111-4111-8111-111111111111"
HOST_BETA = "22222222-2222-4222-8222-222222222222"


def _owner(host_id: str, service_instance_id: str, component_slot_id: str) -> V2TopologyOwner:
    return V2TopologyOwner(
        host_id=host_id,
        service_instance_id=service_instance_id,
        component_slot_id=component_slot_id,
    )


def _projection() -> V2TopologyProjection:
    api = _owner(HOST_ALPHA, "alpha", "api")
    database = _owner(HOST_ALPHA, "alpha", "database")
    worker = _owner(HOST_BETA, "beta", "worker")
    api_endpoint = f"{HOST_ALPHA}/alpha/api/egress"
    database_endpoint = f"{HOST_ALPHA}/alpha/database/ingress"
    worker_endpoint = f"{HOST_BETA}/beta/worker/input"
    return V2TopologyProjection(
        schema_version="infralink.observation-topology/v2",
        filter={"mode": "full"},
        # Deliberately unsorted: renderers own deterministic ordering.
        nodes=(
            V2TopologyNode(
                id=worker_endpoint,
                owner=worker,
                endpoint_id="input",
                protocol=EndpointProtocol.TCP,
                port=9000,
            ),
            V2TopologyNode(
                id=database_endpoint,
                owner=database,
                endpoint_id="ingress",
                protocol=EndpointProtocol.POSTGRESQL,
                port=5432,
            ),
            V2TopologyNode(
                id=api_endpoint,
                owner=api,
                endpoint_id="egress",
                protocol=EndpointProtocol.HTTPS,
                port=443,
            ),
        ),
        edges=(
            V2TopologyEdge(
                id="api-to-database",
                source_endpoint_id=api_endpoint,
                target_endpoint_id=database_endpoint,
                source_owner=api,
                target_owner=database,
                scope=EdgeScope.INTRA_SERVICE,
            ),
            V2TopologyEdge(
                id="api-to-beta",
                source_endpoint_id=api_endpoint,
                target_endpoint_id=worker_endpoint,
                source_owner=api,
                target_owner=worker,
                scope=EdgeScope.INTER_SERVICE,
            ),
        ),
    )


def test_renders_v2_topology_as_nested_mermaid_golden() -> None:
    from infralink.observation.topology_diagrams import render_v2_mermaid

    assert (
        render_v2_mermaid(_projection())
        == f'''flowchart LR
%% infralink.topology.schema_version="infralink.observation-topology/v2"
%% infralink.host.id="{HOST_ALPHA}"
subgraph host_bd7662a5eeb41614["host {HOST_ALPHA}"]
    %% infralink.service.id="{HOST_ALPHA}/alpha"
    subgraph service_4285a6382631a656["service alpha"]
        %% infralink.component.id="{HOST_ALPHA}/alpha/api"
        subgraph component_b07bee6ba587bf28["component api"]
            %% infralink.node.id="{HOST_ALPHA}/alpha/api/egress"
            endpoint_29015cefafb8729b["egress (https:443)"]
        end
        %% infralink.component.id="{HOST_ALPHA}/alpha/database"
        subgraph component_81e36f3f48a5adcc["component database"]
            %% infralink.node.id="{HOST_ALPHA}/alpha/database/ingress"
            endpoint_8470604d0d9f94aa["ingress (postgresql:5432)"]
        end
    end
end
%% infralink.host.id="{HOST_BETA}"
subgraph host_b454f82c5857ebab["host {HOST_BETA}"]
    %% infralink.service.id="{HOST_BETA}/beta"
    subgraph service_8e0598a787f4ebe6["service beta"]
        %% infralink.component.id="{HOST_BETA}/beta/worker"
        subgraph component_d14dfb560762979e["component worker"]
            %% infralink.node.id="{HOST_BETA}/beta/worker/input"
            endpoint_f9950a44efbf7ad2["input (tcp:9000)"]
        end
    end
end
%% infralink.edge.id="api-to-beta"
endpoint_29015cefafb8729b -->|"api-to-beta (inter-service)"| endpoint_f9950a44efbf7ad2
%% infralink.edge.id="api-to-database"
endpoint_29015cefafb8729b -->|"api-to-database (intra-service)"| endpoint_8470604d0d9f94aa
'''
    )


def test_renders_v2_topology_as_nested_dot_golden() -> None:
    from infralink.observation.topology_diagrams import render_v2_dot

    assert (
        render_v2_dot(_projection())
        == f'''digraph "infralink_observation_v2" {{
    graph [rankdir="LR"];
    node [shape="box"];
    subgraph "cluster_host_bd7662a5eeb41614" {{
        graph [id="{HOST_ALPHA}", label="host {HOST_ALPHA}"];
        subgraph "cluster_service_4285a6382631a656" {{
            graph [id="{HOST_ALPHA}/alpha", label="service alpha"];
            subgraph "cluster_component_b07bee6ba587bf28" {{
                graph [id="{HOST_ALPHA}/alpha/api", label="component api"];
                "endpoint_29015cefafb8729b" [id="{HOST_ALPHA}/alpha/api/egress", label="egress (https:443)"];
            }}
            subgraph "cluster_component_81e36f3f48a5adcc" {{
                graph [id="{HOST_ALPHA}/alpha/database", label="component database"];
                "endpoint_8470604d0d9f94aa" [id="{HOST_ALPHA}/alpha/database/ingress", label="ingress (postgresql:5432)"];
            }}
        }}
    }}
    subgraph "cluster_host_b454f82c5857ebab" {{
        graph [id="{HOST_BETA}", label="host {HOST_BETA}"];
        subgraph "cluster_service_8e0598a787f4ebe6" {{
            graph [id="{HOST_BETA}/beta", label="service beta"];
            subgraph "cluster_component_d14dfb560762979e" {{
                graph [id="{HOST_BETA}/beta/worker", label="component worker"];
                "endpoint_f9950a44efbf7ad2" [id="{HOST_BETA}/beta/worker/input", label="input (tcp:9000)"];
            }}
        }}
    }}
    "endpoint_29015cefafb8729b" -> "endpoint_f9950a44efbf7ad2" [id="api-to-beta", label="api-to-beta (inter-service)"];
    "endpoint_29015cefafb8729b" -> "endpoint_8470604d0d9f94aa" [id="api-to-database", label="api-to-database (intra-service)"];
}}
'''
    )


def test_v2_topology_renderers_escape_untrusted_values_and_remain_deterministic() -> None:
    from infralink.observation.topology_diagrams import render_v2_dot, render_v2_mermaid

    unsafe_owner = V2TopologyOwner.model_construct(
        host_id='host"\\\\\nmalicious|[',
        service_instance_id='service"\\\\\nmalicious|[',
        component_slot_id='component"\\\\\nmalicious|[',
    )
    unsafe_node = V2TopologyNode.model_construct(
        id='endpoint"\\\\\nmalicious|[',
        owner=unsafe_owner,
        endpoint_id='endpoint"\\\\\nmalicious|[',
        protocol=EndpointProtocol.TCP,
        port=1,
    )
    unsafe_edge = V2TopologyEdge.model_construct(
        id='edge"\\\\\nmalicious|[',
        source_endpoint_id=unsafe_node.id,
        target_endpoint_id=unsafe_node.id,
        source_owner=unsafe_owner,
        target_owner=unsafe_owner,
        scope=EdgeScope.INTRA_SERVICE,
    )
    projection = V2TopologyProjection.model_construct(
        schema_version="infralink.observation-topology/v2",
        filter={"mode": "full"},
        nodes=(unsafe_node,),
        edges=(unsafe_edge,),
    )

    mermaid = render_v2_mermaid(projection)
    dot = render_v2_dot(projection)

    assert render_v2_mermaid(_projection()) == render_v2_mermaid(
        _projection().model_copy(update={"nodes": tuple(reversed(_projection().nodes))})
    )
    assert render_v2_dot(_projection()) == render_v2_dot(
        _projection().model_copy(update={"edges": tuple(reversed(_projection().edges))})
    )
    assert "\nmalicious" not in mermaid
    assert "\nmalicious" not in dot
    assert "&quot;" in mermaid
    assert "&#92;" in mermaid
    assert "&#10;" in mermaid
    assert "&#124;" in mermaid
    assert "&#91;" in mermaid
    assert '\\"' in dot
    assert re.search(r"endpoint_[0-9a-f]{16}", mermaid)
    assert re.search(r'"endpoint_[0-9a-f]{16}"', dot)
