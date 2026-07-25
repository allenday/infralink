"""Pure serializers and bounded read-only topology queries."""

from __future__ import annotations

import shlex
from dataclasses import dataclass, field
from typing import Any

from infralink.cli.actions import action
from infralink.cli.contracts import (
    AppListResult,
    AppShowResult,
    AppSummary,
    EdgeListResult,
    EdgeShowResult,
    EdgeSummary,
    HostListResult,
    HostShowResult,
    HostSummary,
    ServiceListResult,
    ServiceShowResult,
    ServiceSummary,
)
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.pagination import page_items
from infralink.core.application import Application
from infralink.core.edges import Edge, EdgeSet
from infralink.core.registry import Host, Registry


def entity_not_found(entity_type: str, requested_id: str) -> CliFailure:
    discovery = {
        "host": ["infralink", "hosts"],
        "service": ["infralink", "services"],
        "edge": ["infralink", "edges-list"],
        "app": ["infralink", "app", "list"],
    }[entity_type]
    return CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message=f"{entity_type.title()} not found",
        exit_code=3,
        fix=f"Run {shlex.join(discovery)}",
        details={"entity_type": entity_type, "requested_id": requested_id},
        next_actions=[action("list", discovery, f"List {entity_type} records")],
    )


def host_summary(host: Host, service_preview_limit: int = 128) -> HostSummary:
    services = sorted(set(host.service_names))
    projects = sorted(set(host.projects))
    return HostSummary(
        id=host.uuid,
        canonical_name=host.canonical_name,
        status=host.status.value,
        service_count=len(services),
        services=services[:service_preview_limit],
        services_truncated=len(services) > service_preview_limit,
        project_count=len(projects),
        projects=projects[:64],
        projects_truncated=len(projects) > 64,
    )


@dataclass
class _ServiceIdentity:
    hosts: set[str] = field(default_factory=set)
    ports: set[int] = field(default_factory=set)
    protocols: set[str] = field(default_factory=set)


def _service_identities(registry: Registry, edges: EdgeSet) -> dict[str, _ServiceIdentity]:
    identities: dict[str, _ServiceIdentity] = {}
    for host in registry:
        for service_id in set(host.roles) | set(host.service_names):
            identity = identities.setdefault(service_id, _ServiceIdentity())
            identity.hosts.add(host.uuid)
            config = host.services.get(service_id, {})
            port = config.get("port")
            if isinstance(port, int) and not isinstance(port, bool):
                identity.ports.add(port)
            protocol = config.get("protocol")
            if isinstance(protocol, str):
                identity.protocols.add(protocol)
    for edge in edges:
        identity = identities.setdefault(edge.target_service, _ServiceIdentity())
        identity.hosts.add(edge.target_host)
        if isinstance(edge.target_port, int):
            identity.ports.add(edge.target_port)
        if edge.protocol:
            identity.protocols.add(edge.protocol)
    return identities


def service_summary(service_id: str, identity: _ServiceIdentity) -> ServiceSummary:
    hosts = sorted(identity.hosts)
    ports = sorted(identity.ports)
    protocols = sorted(identity.protocols)
    return ServiceSummary(
        id=service_id,
        host_count=len(hosts),
        host_ids=hosts[:128],
        hosts_truncated=len(hosts) > 128,
        port_count=len(ports),
        ports=ports[:64],
        ports_truncated=len(ports) > 64,
        protocol_count=len(protocols),
        protocols=protocols[:32],
        protocols_truncated=len(protocols) > 32,
    )


def edge_summary(edge: Edge) -> EdgeSummary:
    source: dict[str, Any] = {
        "hosts": "*" if edge.is_wildcard_source() else sorted(edge.source_hosts)
    }
    if edge.source_selector is not None:
        source["selector"] = edge.source_selector
    if edge.source_service is not None:
        source["service"] = edge.source_service
    target = {
        "host": edge.target_host,
        "service": edge.target_service,
        "port": edge.target_port,
    }
    refs = sorted({edge.secret_ref} if edge.secret_ref else set())
    return EdgeSummary.model_validate(
        {
            "id": edge.id,
            "type": edge.type.value,
            "from": source,
            "to": target,
            "protocol": edge.protocol,
            "secret_ref_count": len(refs),
            "secret_refs": refs[:32],
            "secret_refs_truncated": len(refs) > 32,
        }
    )


def _app_edges(application: Application, registry: Registry, edges: EdgeSet) -> list[Edge]:
    return sorted(application.resolve_edges(registry, edges), key=lambda edge: edge.id)


def _app_service_ids(application: Application) -> list[str]:
    return sorted(
        {service_id for member in application.schema.members for service_id in member.services}
    )


def _app_service_identities(
    application: Application,
    registry: Registry,
    app_edges: list[Edge],
) -> dict[str, _ServiceIdentity]:
    identities = {service_id: _ServiceIdentity() for service_id in _app_service_ids(application)}
    for member in application.schema.members:
        host = registry.get(member.host)
        for service_id in member.services:
            identity = identities[service_id]
            identity.hosts.add(member.host)
            config = host.services.get(service_id, {}) if host is not None else {}
            port = config.get("port")
            if isinstance(port, int) and not isinstance(port, bool):
                identity.ports.add(port)
            protocol = config.get("protocol")
            if isinstance(protocol, str):
                identity.protocols.add(protocol)
    for edge in app_edges:
        edge_identity = identities.get(edge.target_service)
        if edge_identity is None:
            continue
        edge_identity.hosts.add(edge.target_host)
        edge_identity.ports.add(edge.target_port)
        if edge.protocol:
            edge_identity.protocols.add(edge.protocol)
    return identities


def app_summary(application: Application, registry: Registry, edges: EdgeSet) -> AppSummary:
    return AppSummary(
        id=application.id,
        service_count=len(_app_service_ids(application)),
        edge_count=len(_app_edges(application, registry, edges)),
    )


def list_hosts(
    registry: Registry,
    *,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
) -> HostListResult:
    items = [host_summary(host) for host in sorted(registry, key=lambda item: item.uuid)]
    page = page_items(items, limit=limit, offset=offset, next_cursor=next_cursor)
    return HostListResult(items=page.items, page=page.page)


def show_host(
    registry: Registry,
    host_id: str,
    *,
    collection: str | None = None,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
    next_cursors: dict[str, str] | None = None,
) -> HostShowResult:
    host = registry.get(host_id)
    if host is None:
        raise entity_not_found("host", host_id)
    selected = collection or "services"
    services = sorted(set(host.service_names))
    projects = sorted(set(host.projects))
    cursors = next_cursors or {}
    service_page = page_items(
        services,
        limit=limit,
        offset=offset if selected == "services" else 0,
        next_cursor=cursors.get("services", next_cursor if selected == "services" else None),
    )
    project_page = page_items(
        projects,
        limit=limit,
        offset=offset if selected == "projects" else 0,
        next_cursor=cursors.get("projects", next_cursor if selected == "projects" else None),
    )
    return HostShowResult(
        host=host_summary(host),
        services=service_page,
        projects=project_page,
    )


def list_services(
    registry: Registry,
    edges: EdgeSet,
    *,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
) -> ServiceListResult:
    identities = _service_identities(registry, edges)
    items = [
        service_summary(service_id, identities[service_id]) for service_id in sorted(identities)
    ]
    page = page_items(items, limit=limit, offset=offset, next_cursor=next_cursor)
    return ServiceListResult(items=page.items, page=page.page)


def show_service(
    registry: Registry,
    edges: EdgeSet,
    service_id: str,
    *,
    collection: str | None = None,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
    next_cursors: dict[str, str] | None = None,
) -> ServiceShowResult:
    identities = _service_identities(registry, edges)
    identity = identities.get(service_id)
    if identity is None:
        raise entity_not_found("service", service_id)
    selected = collection or "hosts"
    hosts = sorted(identity.hosts)
    ports = sorted(identity.ports)
    protocols = sorted(identity.protocols)
    cursors = next_cursors or {}
    return ServiceShowResult(
        service=service_summary(service_id, identity),
        hosts=page_items(
            hosts,
            limit=limit,
            offset=offset if selected == "hosts" else 0,
            next_cursor=cursors.get("hosts", next_cursor if selected == "hosts" else None),
        ),
        ports=page_items(
            ports,
            limit=limit,
            offset=offset if selected == "ports" else 0,
            next_cursor=cursors.get("ports", next_cursor if selected == "ports" else None),
        ),
        protocols=page_items(
            protocols,
            limit=limit,
            offset=offset if selected == "protocols" else 0,
            next_cursor=cursors.get("protocols", next_cursor if selected == "protocols" else None),
        ),
    )


def list_edges(
    edges: EdgeSet,
    *,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
) -> EdgeListResult:
    items = [edge_summary(edge) for edge in sorted(edges, key=lambda item: item.id)]
    page = page_items(items, limit=limit, offset=offset, next_cursor=next_cursor)
    return EdgeListResult(items=page.items, page=page.page)


def show_edge(
    edges: EdgeSet,
    edge_id: str,
    *,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
) -> EdgeShowResult:
    edge = edges.get(edge_id)
    if edge is None:
        raise entity_not_found("edge", edge_id)
    summary = edge_summary(edge)
    return EdgeShowResult(
        edge=summary,
        secret_refs=page_items(
            summary.secret_refs,
            limit=limit,
            offset=offset,
            next_cursor=next_cursor,
        ),
    )


def list_apps(
    registry: Registry,
    edges: EdgeSet,
    *,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
) -> AppListResult:
    applications = sorted(registry.applications, key=lambda item: item.id)
    items = [app_summary(application, registry, edges) for application in applications]
    page = page_items(items, limit=limit, offset=offset, next_cursor=next_cursor)
    return AppListResult(items=page.items, page=page.page)


def show_app(
    registry: Registry,
    edges: EdgeSet,
    app_id: str,
    *,
    collection: str | None = None,
    limit: int = 100,
    offset: int = 0,
    next_cursor: str | None = None,
    next_cursors: dict[str, str] | None = None,
) -> AppShowResult:
    application = registry.applications.get_application(app_id)
    if application is None:
        raise entity_not_found("app", app_id)
    selected = collection or "services"
    app_edges = _app_edges(application, registry, edges)
    identities = _app_service_identities(application, registry, app_edges)
    services = [
        service_summary(service_id, identities[service_id])
        for service_id in _app_service_ids(application)
    ]
    edge_items = [edge_summary(edge) for edge in app_edges]
    cursors = next_cursors or {}
    return AppShowResult(
        app=app_summary(application, registry, edges),
        services=page_items(
            services,
            limit=limit,
            offset=offset if selected == "services" else 0,
            next_cursor=cursors.get("services", next_cursor if selected == "services" else None),
        ),
        edges=page_items(
            edge_items,
            limit=limit,
            offset=offset if selected == "edges" else 0,
            next_cursor=cursors.get("edges", next_cursor if selected == "edges" else None),
        ),
    )
