"""Transport-neutral bounded topology read operations."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Literal

from agent_surface import OperationError
from pydantic import Field, StrictInt

from infralink.cli.contracts import (
    AppListResult,
    AppShowResult,
    EdgeListResult,
    EdgeShowResult,
    HostListResult,
    HostShowResult,
    ServiceListResult,
    ServiceShowResult,
)
from infralink.cli.pagination import production_cursor_codec
from infralink.cli.queries import (
    list_apps,
    list_edges,
    list_hosts,
    list_services,
    show_app,
    show_edge,
    show_host,
    show_service,
)
from infralink.operator_sources import (
    SourceRequest,
    load_registry,
    load_sources,
    resolve_registry_companion,
)


class PagedTopologyRequest(SourceRequest):
    """Bound one topology collection without transport-owned pagination."""

    limit: StrictInt = Field(default=20, ge=1, le=1000)
    cursor: str | None = None


class HostShowRequest(PagedTopologyRequest):
    host_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})
    collection: Literal["services", "projects"] | None = None


class ServiceShowRequest(PagedTopologyRequest):
    service_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})
    app_id: str | None = None
    collection: Literal["hosts", "ports", "protocols", "edges"] | None = None


class EdgeShowRequest(PagedTopologyRequest):
    edge_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})


class AppShowRequest(PagedTopologyRequest):
    app_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})
    collection: Literal["services", "edges"] | None = None


def list_declared_hosts(request: SourceRequest) -> HostListResult:
    return list_hosts(load_registry(request).registry)


def list_declared_services(request: SourceRequest) -> ServiceListResult:
    sources = load_sources(request)
    return list_services(sources.registry, sources.edges)


def list_declared_edges(request: SourceRequest) -> EdgeListResult:
    return list_edges(load_sources(request).edges)


def list_declared_apps(request: SourceRequest) -> AppListResult:
    sources = load_sources(request)
    return list_apps(sources.registry, sources.edges)


def show_declared_host(request: HostShowRequest) -> HostShowResult:
    registry = load_registry(request)
    edges_path = _host_edges_path(request, registry.registry_source_path)
    selected = _selected_collection(request.collection, request.cursor, ("services", "projects"))
    fingerprint = _fingerprint(
        registry_path=registry.registry_source_path,
        edges_path=edges_path,
        registry=registry.registry,
        edges=None,
        include_edges=False,
        identifiers={"host_id": request.host_id},
    )
    offset = _page_offset(
        request.cursor,
        command="host show",
        collection=selected,
        fingerprint=fingerprint,
    )
    result = show_host(
        registry.registry,
        request.host_id,
        collection=selected,
        limit=request.limit,
        offset=offset,
    )
    _attach_cursors(
        result,
        command="host show",
        collections=("services", "projects"),
        selected=selected,
        offset=offset,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    return result


def show_declared_service(request: ServiceShowRequest) -> ServiceShowResult:
    sources = load_sources(request)
    selected = _selected_collection(
        request.collection, request.cursor, ("hosts", "ports", "protocols", "edges")
    )
    identifiers = {"service_id": request.service_id}
    if request.app_id is not None:
        identifiers["app_id"] = request.app_id
    fingerprint = _fingerprint(
        registry_path=sources.registry_source_path,
        edges_path=sources.edges_source_path,
        registry=sources.registry,
        edges=sources.edges,
        include_edges=True,
        identifiers=identifiers,
    )
    offset = _page_offset(
        request.cursor,
        command="service show",
        collection=selected,
        fingerprint=fingerprint,
    )
    result = show_service(
        sources.registry,
        sources.edges,
        request.service_id,
        app_id=request.app_id,
        collection=selected,
        limit=request.limit,
        offset=offset,
    )
    _attach_cursors(
        result,
        command="service show",
        collections=("hosts", "ports", "protocols", "edges"),
        selected=selected,
        offset=offset,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    return result


def show_declared_edge(request: EdgeShowRequest) -> EdgeShowResult:
    sources = load_sources(request)
    fingerprint = _fingerprint(
        registry_path=sources.registry_source_path,
        edges_path=sources.edges_source_path,
        registry=sources.registry,
        edges=sources.edges,
        include_edges=True,
        include_registry=False,
        identifiers={"edge_id": request.edge_id},
    )
    offset = _page_offset(
        request.cursor,
        command="edge show",
        collection="secret_refs",
        fingerprint=fingerprint,
    )
    result = show_edge(sources.edges, request.edge_id, limit=request.limit, offset=offset)
    _attach_cursors(
        result,
        command="edge show",
        collections=("secret_refs",),
        selected="secret_refs",
        offset=offset,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    return result


def show_declared_app(request: AppShowRequest) -> AppShowResult:
    sources = load_sources(request)
    selected = _selected_collection(request.collection, request.cursor, ("services", "edges"))
    fingerprint = _fingerprint(
        registry_path=sources.registry_source_path,
        edges_path=sources.edges_source_path,
        registry=sources.registry,
        edges=sources.edges,
        include_edges=True,
        identifiers={"app_id": request.app_id},
    )
    offset = _page_offset(
        request.cursor,
        command="app show",
        collection=selected,
        fingerprint=fingerprint,
    )
    result = show_app(
        sources.registry,
        sources.edges,
        request.app_id,
        collection=selected,
        limit=request.limit,
        offset=offset,
    )
    _attach_cursors(
        result,
        command="app show",
        collections=("services", "edges"),
        selected=selected,
        offset=offset,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    return result


def _selected_collection(selected: str | None, cursor: str | None, allowed: tuple[str, ...]) -> str:
    if cursor is not None and len(allowed) > 1 and selected is None:
        _invalid_cursor()
    chosen = selected or allowed[0]
    if chosen not in allowed:
        _invalid_cursor()
    return chosen


def _page_offset(cursor: str | None, *, command: str, collection: str, fingerprint: str) -> int:
    if cursor is None:
        return 0
    try:
        return production_cursor_codec().decode(cursor, command, collection, fingerprint)
    except Exception as error:
        from infralink.cli.errors import CliFailure

        if isinstance(error, CliFailure):
            raise OperationError(error.code.value, error.message, fix=error.fix) from None
        raise


def _attach_cursors(
    result: Any,
    *,
    command: str,
    collections: tuple[str, ...],
    selected: str,
    offset: int,
    limit: int,
    fingerprint: str,
) -> None:
    codec = None
    for collection in collections:
        page = result.page if collection == "items" else getattr(result, collection).page
        collection_offset = offset if collection == selected else 0
        if page.total is None or collection_offset + page.returned >= page.total:
            continue
        if codec is None:
            codec = production_cursor_codec()
        page.next_cursor = codec.encode(command, collection, collection_offset + limit, fingerprint)


def _fingerprint(
    *,
    registry_path: Path,
    edges_path: Path | None,
    registry: Any,
    edges: Any,
    include_edges: bool,
    include_registry: bool = True,
    identifiers: dict[str, str],
) -> str:
    snapshot: dict[str, Any] = {
        "registry_path": str(registry_path),
        "edges_path": str(edges_path),
        "identifiers": identifiers,
    }
    if include_registry:
        snapshot["hosts"] = [
            host.to_dict() for host in sorted(registry, key=lambda item: item.uuid)
        ]
        snapshot["applications"] = [
            application.to_dict()
            for application in sorted(registry.applications, key=lambda item: item.id)
        ]
    if include_edges:
        assert edges is not None
        snapshot["edges"] = [edge.to_dict() for edge in sorted(edges, key=lambda item: item.id)]
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def _invalid_cursor() -> None:
    from infralink.cli.pagination import invalid_cursor

    error = invalid_cursor()
    raise OperationError(error.code.value, error.message, fix=error.fix)


def _host_edges_path(request: SourceRequest, registry_source_path: Path) -> Path | None:
    """Retain legacy host-show cursor binding without loading edge declarations."""
    if request.edges is not None:
        return request.edges.expanduser()
    try:
        return resolve_registry_companion(registry_source_path, filename="edges.yml")
    except OperationError:
        return None
