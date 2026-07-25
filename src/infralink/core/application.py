from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

if TYPE_CHECKING:
    from infralink.core.edges import Edge, EdgeSet
    from infralink.core.registry import Registry

from infralink.core.schema import ApplicationSchema, ApplicationSetSchema


class Application:
    """Logical grouping of hosts, services, and edges."""

    def __init__(self, id: str, schema: ApplicationSchema) -> None:
        self.id = id
        self.schema = schema

    @property
    def description(self) -> str | None:
        return self.schema.description

    def get_member_host_uuids(self) -> list[str]:
        """Get UUIDs of all hosts that are members of this application."""
        return [m.host for m in self.schema.members]

    def resolve_edges(self, registry: Registry, all_edges: EdgeSet) -> list[Edge]:
        """
        Resolve edges belonging to this application.

        If edges is "auto", derives from members. Otherwise uses explicit list.
        """
        if isinstance(self.schema.edges, list):
            return [edge for eid in self.schema.edges if (edge := all_edges.get(eid)) is not None]

        # Auto-derivation: Edge is part of app if target host is a member
        # AND at least one resolved source host is a member.
        member_hosts = set(self.get_member_host_uuids())
        app_edges = []

        from infralink.core.resolver import EdgeResolver

        resolver = EdgeResolver(registry, all_edges)

        for edge in all_edges:
            if edge.target_host in member_hosts:
                try:
                    src_hosts = resolver.resolve_source_hosts(edge.id)
                    src_uuids = {h.uuid for h in src_hosts}
                    if not src_uuids.isdisjoint(member_hosts):
                        app_edges.append(edge)
                except Exception:
                    # If edge doesn't resolve, it can't be part of the app
                    continue

        return app_edges

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "members": [m.model_dump() for m in self.schema.members],
            "edges": self.schema.edges,
            "health": self.schema.health.model_dump(),
        }


class ApplicationSet:
    """Collection of infrastructure applications."""

    def __init__(self, applications: list[Application], schema_version: str = "1.0") -> None:
        self._applications = {app.id: app for app in applications}
        self._schema_version = schema_version

    @classmethod
    def load(cls, path: str | Path) -> ApplicationSet:
        """Load applications from YAML file."""
        path = Path(path)
        if not path.exists():
            return cls([], "1.0")

        with path.open() as f:
            data = yaml.safe_load(f)

        if not data:
            return cls([], "1.0")

        # Validate with schema
        schema = ApplicationSetSchema(**data)

        apps = [
            Application(app_id, app_schema) for app_id, app_schema in schema.applications.items()
        ]

        return cls(apps, schema.schema_version)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ApplicationSet:
        """Create application set from dictionary."""
        schema = ApplicationSetSchema(**data)
        apps = [
            Application(app_id, app_schema) for app_id, app_schema in schema.applications.items()
        ]
        return cls(apps, schema.schema_version)

    def get_application(self, app_id: str) -> Application | None:
        return self._applications.get(app_id)

    def __iter__(self) -> Iterator[Application]:
        return iter(self._applications.values())

    def __len__(self) -> int:
        return len(self._applications)
