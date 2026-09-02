"""Transport-neutral declared edge resolution and health operations."""

from __future__ import annotations

import json
from typing import Literal

from agent_surface import OperationError
from pydantic import Field, StrictInt

from infralink.cli.contracts import (
    CheckCommandResult,
    CheckResult,
    CheckSummary,
    Endpoint,
    ResolveResult,
)
from infralink.cli.pagination import page_items
from infralink.cli.queries import edge_summary
from infralink.core.resolver import EdgeResolver, ResolutionError
from infralink.health.checks import check_edge_health, normalize_health_result
from infralink.operator_operations.topology import (
    PagedTopologyRequest,
    _attach_cursors,
    _fingerprint,
    _page_offset,
)
from infralink.operator_sources import LoadedSources, SourceRequest, load_sources


class EdgeCheckRequest(PagedTopologyRequest):
    """Select bounded declared edges for live reachability checks."""

    edge_ids: tuple[str, ...] = Field(
        default_factory=tuple,
        json_schema_extra={"cli": {"options": ["--edge"]}},
    )
    edge_type: Literal["database", "queue", "cluster", "telemetry", "monitoring", "api"] | None = (
        Field(
            default=None,
            json_schema_extra={"cli": {"options": ["--type"]}},
        )
    )
    criticality: Literal["critical", "high", "medium", "low"] | None = None
    critical_only: bool = False
    timeout: StrictInt = Field(default=5, ge=1)
    collection: Literal["checks"] | None = None


class EdgeResolveRequest(SourceRequest):
    """Resolve one declared edge to its selected endpoint."""

    edge_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})
    user: str | None = None
    database: str | None = None
    prefer_ip: Literal["tailscale", "public", "private"] = "tailscale"


def check_declared_edges(request: EdgeCheckRequest) -> CheckCommandResult:
    """Perform bounded reachability checks against selected declared edges."""
    from infralink.core.schema import Criticality, EdgeType

    sources = _load_edge_sources(request)
    resolver = EdgeResolver(sources.registry, sources.edges)
    selected_edges = list(sources.edges)
    if request.edge_ids:
        selected_edges = [edge for edge in selected_edges if edge.id in request.edge_ids]
    if request.edge_type is not None:
        selected_edges = [
            edge for edge in selected_edges if edge.type == EdgeType(request.edge_type)
        ]
    if request.criticality is not None:
        selected_edges = [
            edge for edge in selected_edges if edge.criticality == Criticality(request.criticality)
        ]
    if request.critical_only:
        selected_edges = [edge for edge in selected_edges if edge.is_critical]
    selected_edges.sort(key=lambda edge: edge.id)

    checks = [
        CheckResult(
            edge_id=result.edge_id,
            healthy=result.healthy,
            status=normalize_health_result(result)[0],
            latency_ms=result.latency_ms,
            error_code=normalize_health_result(result)[1],
        )
        for edge in selected_edges
        for result in (check_edge_health(edge, resolver, timeout=request.timeout),)
    ]
    fingerprint = _fingerprint(
        registry_path=sources.registry_source_path,
        edges_path=sources.edges_source_path,
        registry=sources.registry,
        edges=sources.edges,
        include_edges=True,
        identifiers={
            "requested_edge_ids": json.dumps(sorted(request.edge_ids)),
            "selected_edge_ids": json.dumps([edge.id for edge in selected_edges]),
            "edge_type": str(request.edge_type),
            "criticality": str(request.criticality),
            "critical_only": str(request.critical_only),
            "timeout": str(request.timeout),
        },
    )
    offset = _page_offset(
        request.cursor,
        command="check",
        collection="checks",
        fingerprint=fingerprint,
    )
    healthy_count = sum(check.healthy for check in checks)
    result = CheckCommandResult(
        healthy=healthy_count == len(checks),
        checks=page_items(checks, limit=request.limit, offset=offset, next_cursor=None),
        summary=CheckSummary(
            total=len(checks), healthy=healthy_count, unhealthy=len(checks) - healthy_count
        ),
    )
    result._failed_edge_id = next((check.edge_id for check in checks if not check.healthy), None)
    _attach_cursors(
        result,
        command="check",
        collections=("checks",),
        selected="checks",
        offset=offset,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    return result


def resolve_declared_edge(request: EdgeResolveRequest) -> ResolveResult:
    """Return one resolved endpoint without exposing secret material."""
    sources = _load_edge_sources(request)
    resolver = EdgeResolver(sources.registry, sources.edges)
    if sources.edges.get(request.edge_id) is None:
        raise OperationError(
            "entity_not_found",
            "Edge not found",
            details=({"entity_type": "edge", "requested_id": request.edge_id},),
            fix="Run infralink edge list.",
        )
    try:
        edge = resolver.get_edge(request.edge_id)
        return ResolveResult(
            edge=edge_summary(edge),
            endpoint=Endpoint(
                host=resolver.get_target_ip(request.edge_id, request.prefer_ip),
                port=resolver.get_target_port(request.edge_id),
                protocol=edge.protocol,
            ),
            connection_template=resolver.get_connection_template(
                request.edge_id,
                user=request.user,
                database=request.database,
                prefer_ip=request.prefer_ip,
            ),
            secret_refs=page_items(
                [edge.secret_ref] if edge.secret_ref else [],
                limit=100,
                offset=0,
                next_cursor=None,
            ),
        )
    except ResolutionError:
        raise OperationError(
            "input_load_failed",
            "Edge could not be resolved",
            details=(),
            fix="Verify the edge and its target host declarations.",
        ) from None


def _load_edge_sources(request: EdgeCheckRequest | EdgeResolveRequest) -> LoadedSources:
    """Retain the legacy edge-command source error taxonomy at the new boundary."""
    try:
        return load_sources(request)
    except OperationError as error:
        if error.code not in {"source_not_found", "source_invalid"}:
            raise
        details = error.details
        if len(details) == 1 and isinstance(details[0], dict):
            details = (dict(details[0]),)
            source = details[0].get("source")
            selected = getattr(request, source, None) if isinstance(source, str) else None
            if selected is not None:
                details[0]["path"] = str(selected)
        raise OperationError(
            "input_load_failed",
            "Declared topology source could not be loaded",
            details=details,
            fix=error.fix,
        ) from None
