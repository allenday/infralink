"""Typed, declaration-only topology projection for observation v2 renderers."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from pydantic import Field, ValidationError, model_validator

from infralink.observation.models import (
    CanonicalId,
    Endpoint,
    EndpointProtocol,
    HostId,
    StrictModel,
)
from infralink.observation.models_v2 import (
    ComponentEdge,
    EdgeScope,
    parse_component_endpoint_ref,
)
from infralink.observation.v2 import ObservationV2Document, validate_v2_documents

_MAX_TOPOLOGY_ITEMS = 4096

__all__ = [
    "V2TopologyBoundsError",
    "V2TopologyEdge",
    "V2TopologyFilter",
    "V2TopologyNode",
    "V2TopologyOwner",
    "V2TopologyProjection",
    "V2TopologyServiceInstanceRef",
    "project_v2_topology",
]


class V2TopologyBoundsError(ValueError):
    """A declaration set exceeds the bounded topology projection contract."""


class V2TopologyOwner(StrictModel):
    """The declaration-owned component that owns one endpoint or edge side."""

    host_id: HostId
    service_instance_id: CanonicalId
    component_slot_id: CanonicalId


class V2TopologyNode(StrictModel):
    """One resolved endpoint identity, without its address or runtime state."""

    id: str
    owner: V2TopologyOwner
    endpoint_id: CanonicalId
    protocol: EndpointProtocol
    port: int = Field(ge=1, le=65535)


class V2TopologyEdge(StrictModel):
    """One typed component edge and its declaration-owned endpoint identities."""

    id: CanonicalId
    source_endpoint_id: str
    target_endpoint_id: str
    source_owner: V2TopologyOwner
    target_owner: V2TopologyOwner
    scope: EdgeScope


class V2TopologyServiceInstanceRef(StrictModel):
    """The canonical host-qualified identity of one service instance."""

    host_id: HostId
    service_instance_id: CanonicalId


class V2TopologyFilter(StrictModel):
    """One full or focal declaration filter applied before direct-neighbour expansion."""

    mode: Literal["full", "host", "service"]
    host_id: HostId | None = None
    service_instance: V2TopologyServiceInstanceRef | None = None

    @model_validator(mode="after")
    def require_one_consistent_scope(self) -> V2TopologyFilter:
        if self.mode == "full" and self.host_id is None and self.service_instance is None:
            return self
        if self.mode == "host" and self.host_id is not None and self.service_instance is None:
            return self
        if self.mode == "service" and self.host_id is None and self.service_instance is not None:
            return self
        raise ValueError("topology filter mode must match exactly one focal declaration")


class V2TopologyProjection(StrictModel):
    """A bounded renderer-neutral V2 topology graph with deterministic ordering."""

    schema_version: Literal["infralink.observation-topology/v2"]
    filter: V2TopologyFilter
    nodes: tuple[V2TopologyNode, ...] = Field(max_length=_MAX_TOPOLOGY_ITEMS)
    edges: tuple[V2TopologyEdge, ...] = Field(max_length=_MAX_TOPOLOGY_ITEMS)


def project_v2_topology(
    documents: Iterable[ObservationV2Document],
    *,
    focal_host_id: str | None = None,
    focal_service_instance_ref: str | None = None,
) -> V2TopologyProjection:
    """Project validated V2 declarations without addresses, resources, or runtime data.

    A focal service is the canonical ``<host_uuid>/<service_instance_id>``
    declaration identity. Focal nodes expand only by direct component-edge
    neighbours.
    """

    selected_filter = _topology_filter(focal_host_id, focal_service_instance_ref)
    document_list = tuple(documents)
    endpoint_refs = validate_v2_documents(document_list)
    _resolve_filter(selected_filter, document_list)
    _enforce_projection_bounds(
        endpoint_count=len(endpoint_refs),
        edge_count=sum(len(document.component_edges) for document in document_list),
    )
    all_nodes = {
        endpoint_ref: _topology_node(endpoint_ref, endpoint)
        for endpoint_ref, endpoint in endpoint_refs.items()
    }
    all_edges = tuple(
        sorted(
            (
                _topology_edge(edge)
                for document in document_list
                for edge in document.component_edges
            ),
            key=lambda edge: edge.id,
        )
    )

    selected_ids = {
        node.id for node in all_nodes.values() if _matches_filter(node, selected_filter)
    }
    selected_edges = tuple(
        edge
        for edge in all_edges
        if edge.source_endpoint_id in selected_ids or edge.target_endpoint_id in selected_ids
    )
    selected_ids.update(
        endpoint_id
        for edge in selected_edges
        for endpoint_id in (edge.source_endpoint_id, edge.target_endpoint_id)
    )
    nodes = tuple(
        sorted((all_nodes[node_id] for node_id in selected_ids), key=lambda node: node.id)
    )
    return V2TopologyProjection(
        schema_version="infralink.observation-topology/v2",
        filter=selected_filter,
        nodes=nodes,
        edges=selected_edges,
    )


def _topology_filter(
    focal_host_id: str | None, focal_service_instance_ref: str | None
) -> V2TopologyFilter:
    if focal_host_id is not None and focal_service_instance_ref is not None:
        raise ValueError("choose a focal host or focal service, not both")
    if focal_host_id is not None:
        return V2TopologyFilter(mode="host", host_id=focal_host_id)
    if focal_service_instance_ref is not None:
        return V2TopologyFilter(
            mode="service",
            service_instance=_parse_focal_service_instance_ref(focal_service_instance_ref),
        )
    return V2TopologyFilter(mode="full")


def _parse_focal_service_instance_ref(value: str) -> V2TopologyServiceInstanceRef:
    if not isinstance(value, str):
        raise ValueError("focal service must use <host_uuid>/<service_instance_id>")
    parts = value.split("/")
    if len(parts) != 2:
        raise ValueError("focal service must use <host_uuid>/<service_instance_id>")
    try:
        return V2TopologyServiceInstanceRef(host_id=parts[0], service_instance_id=parts[1])
    except ValidationError as error:
        raise ValueError("focal service must use <host_uuid>/<service_instance_id>") from error


def _resolve_filter(
    selected_filter: V2TopologyFilter, documents: Iterable[ObservationV2Document]
) -> None:
    instances = {
        (instance.host_id, instance.id)
        for document in documents
        for instance in document.service_instances
    }
    if selected_filter.mode == "host":
        if selected_filter.host_id not in {host_id for host_id, _ in instances}:
            raise ValueError("focal host is not declared")
        return
    if selected_filter.mode == "service":
        service = selected_filter.service_instance
        assert service is not None
        if (service.host_id, service.service_instance_id) not in instances:
            raise ValueError("focal service is not declared")


def _enforce_projection_bounds(*, endpoint_count: int, edge_count: int) -> None:
    if endpoint_count > _MAX_TOPOLOGY_ITEMS or edge_count > _MAX_TOPOLOGY_ITEMS:
        raise V2TopologyBoundsError(
            f"topology item bound of {_MAX_TOPOLOGY_ITEMS} exceeded "
            f"(endpoints={endpoint_count}, edges={edge_count})"
        )


def _topology_node(endpoint_ref: str, endpoint: Endpoint) -> V2TopologyNode:
    host_id, service_instance_id, component_slot_id, endpoint_id = parse_component_endpoint_ref(
        endpoint_ref
    )
    return V2TopologyNode(
        id=endpoint_ref,
        owner=V2TopologyOwner(
            host_id=host_id,
            service_instance_id=service_instance_id,
            component_slot_id=component_slot_id,
        ),
        endpoint_id=endpoint_id,
        protocol=endpoint.protocol,
        port=endpoint.port,
    )


def _topology_edge(edge: ComponentEdge) -> V2TopologyEdge:
    source_host_id, source_service_id, source_component_id, _ = parse_component_endpoint_ref(
        edge.source_endpoint_id
    )
    target_host_id, target_service_id, target_component_id, _ = parse_component_endpoint_ref(
        edge.target_endpoint_id
    )
    return V2TopologyEdge(
        id=edge.id,
        source_endpoint_id=edge.source_endpoint_id,
        target_endpoint_id=edge.target_endpoint_id,
        source_owner=V2TopologyOwner(
            host_id=source_host_id,
            service_instance_id=source_service_id,
            component_slot_id=source_component_id,
        ),
        target_owner=V2TopologyOwner(
            host_id=target_host_id,
            service_instance_id=target_service_id,
            component_slot_id=target_component_id,
        ),
        scope=edge.scope,
    )


def _matches_filter(node: V2TopologyNode, selected_filter: V2TopologyFilter) -> bool:
    if selected_filter.mode == "full":
        return True
    if selected_filter.mode == "host":
        return node.owner.host_id == selected_filter.host_id
    service = selected_filter.service_instance
    assert service is not None
    return (
        node.owner.host_id == service.host_id
        and node.owner.service_instance_id == service.service_instance_id
    )
