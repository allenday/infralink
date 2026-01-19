"""Edge management for infrastructure topology."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterator

import yaml

from infralink.core.schema import (
    Criticality,
    EdgeSchema,
    EdgeSetSchema,
    EdgeType,
    HealthCheckConfig,
)


class Edge:
    """Represents a connection between infrastructure nodes."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        # Handle 'from' -> 'from_' alias
        if "from" in data and "from_" not in data:
            data["from_"] = data.pop("from")
        self._schema = EdgeSchema(**data)

    @property
    def id(self) -> str:
        return self._schema.id

    @property
    def type(self) -> EdgeType:
        return self._schema.type

    @property
    def source_hosts(self) -> list[str]:
        """Get source host UUIDs."""
        hosts = self._schema.from_.hosts
        if hosts == "*":
            return []  # Wildcard - must be resolved against registry
        return list(hosts) if isinstance(hosts, list) else []

    @property
    def source_selector(self) -> dict[str, Any] | None:
        """Get source selector for dynamic host matching."""
        return self._schema.from_.selector

    @property
    def source_service(self) -> str | None:
        return self._schema.from_.service

    @property
    def target_host(self) -> str:
        """Target host UUID."""
        return self._schema.to.host

    @property
    def target_service(self) -> str:
        return self._schema.to.service

    @property
    def target_port(self) -> int:
        return self._schema.to.port

    @property
    def protocol(self) -> str | None:
        return self._schema.protocol

    @property
    def criticality(self) -> Criticality:
        return self._schema.metadata.criticality

    @property
    def is_critical(self) -> bool:
        return self._schema.metadata.criticality == Criticality.CRITICAL

    @property
    def purpose(self) -> str | None:
        return self._schema.metadata.purpose

    @property
    def healthcheck(self) -> HealthCheckConfig:
        return self._schema.healthcheck

    @property
    def auth_type(self) -> str:
        return self._schema.auth.type

    @property
    def secret_ref(self) -> str | None:
        return self._schema.auth.secret_ref

    def is_wildcard_source(self) -> bool:
        """Check if source is wildcard (all hosts)."""
        return self._schema.from_.hosts == "*"

    def matches_source(self, host_uuid: str) -> bool:
        """Check if a host UUID is a source for this edge."""
        if self.is_wildcard_source():
            return True
        return host_uuid in self.source_hosts

    def to_dict(self) -> dict[str, Any]:
        return self._data.copy()

    def __repr__(self) -> str:
        return (
            f"Edge({self.id}, {self.type.value}: "
            f"{len(self.source_hosts)} sources -> {self.target_service}:{self.target_port})"
        )


class EdgeSet:
    """
    Collection of infrastructure edges.

    Manages edge definitions and provides query capabilities.
    """

    def __init__(self, edges: list[Edge], schema_version: str = "1.0") -> None:
        self._edges = edges
        self._schema_version = schema_version
        self._id_index: dict[str, Edge] = {e.id: e for e in edges}
        self._type_index: dict[EdgeType, list[Edge]] = {}
        for edge in edges:
            self._type_index.setdefault(edge.type, []).append(edge)

    @classmethod
    def load(cls, path: str | Path) -> EdgeSet:
        """Load edges from YAML file."""
        path = Path(path)
        with path.open() as f:
            data = yaml.safe_load(f)

        # Validate with schema
        schema = EdgeSetSchema(**data)

        edges = [Edge(e.model_dump(by_alias=True)) for e in schema.edges]

        return cls(edges, schema.schema_version)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EdgeSet:
        """Create edge set from dictionary."""
        edges_data = data.get("edges", [])
        edges = [Edge(e) for e in edges_data]
        return cls(edges, data.get("schema_version", "1.0"))

    @classmethod
    def from_registry(cls, registry_data: dict[str, Any]) -> EdgeSet:
        """Extract edges from a registry file (if embedded)."""
        edges_data = registry_data.get("edges", [])
        if not edges_data:
            return cls([])
        return cls.from_dict({"edges": edges_data})

    def get(self, edge_id: str) -> Edge | None:
        """Get edge by ID."""
        return self._id_index.get(edge_id)

    def by_type(self, edge_type: EdgeType) -> list[Edge]:
        """Get all edges of a specific type."""
        return self._type_index.get(edge_type, [])

    def by_criticality(self, criticality: Criticality) -> list[Edge]:
        """Get all edges with specific criticality."""
        return [e for e in self._edges if e.criticality == criticality]

    def critical_edges(self) -> list[Edge]:
        """Get all critical edges."""
        return self.by_criticality(Criticality.CRITICAL)

    def targeting_host(self, host_uuid: str) -> list[Edge]:
        """Get all edges targeting a specific host."""
        return [e for e in self._edges if e.target_host == host_uuid]

    def from_host(self, host_uuid: str) -> list[Edge]:
        """Get all edges originating from a specific host."""
        return [e for e in self._edges if e.matches_source(host_uuid)]

    def targeting_service(self, service: str) -> list[Edge]:
        """Get all edges targeting a specific service."""
        return [e for e in self._edges if e.target_service == service]

    def database_edges(self) -> list[Edge]:
        """Get all database-type edges."""
        return self.by_type(EdgeType.DATABASE)

    def queue_edges(self) -> list[Edge]:
        """Get all queue-type edges."""
        return self.by_type(EdgeType.QUEUE)

    def monitoring_edges(self) -> list[Edge]:
        """Get all monitoring-type edges."""
        return self.by_type(EdgeType.MONITORING)

    @property
    def schema_version(self) -> str:
        return self._schema_version

    def __len__(self) -> int:
        return len(self._edges)

    def __iter__(self) -> Iterator[Edge]:
        return iter(self._edges)

    def __contains__(self, edge_id: str) -> bool:
        return edge_id in self._id_index
