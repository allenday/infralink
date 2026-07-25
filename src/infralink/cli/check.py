"""Health check CLI command."""

from __future__ import annotations

import hashlib
import json

import click

from infralink.cli.contracts import CheckCommandResult, CheckResult, CheckSummary
from infralink.cli.main import (
    Context,
    _active_collection,
    _attach_next_cursors,
    _emit_query_result,
    _page_offset,
    _page_options,
    _topology_fingerprint,
    pass_context,
)
from infralink.cli.pagination import page_items
from infralink.health.checks import check_edge_health, normalize_health_result


@click.command()
@click.option(
    "--edge",
    "-e",
    "edge_ids",
    multiple=True,
    help="Specific edge ID(s) to check (default: all)",
)
@click.option(
    "--type",
    "-t",
    "edge_type",
    type=click.Choice(["database", "queue", "cluster", "telemetry", "monitoring", "api"]),
    help="Filter by edge type",
)
@click.option(
    "--criticality",
    "-c",
    type=click.Choice(["critical", "high", "medium", "low"]),
    help="Filter by criticality",
)
@click.option(
    "--critical-only",
    is_flag=True,
    help="Only check critical edges",
)
@click.option(
    "--timeout",
    default=5,
    type=int,
    help="Health check timeout in seconds",
)
@_page_options
@pass_context
def check(
    ctx: Context,
    edge_ids: tuple[str, ...],
    edge_type: str | None,
    criticality: str | None,
    critical_only: bool,
    timeout: int,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> int:
    """
    Check health of infrastructure edges.

    Performs connectivity checks on declared edges and reports status.

    Examples:

        # Check all edges
        infralink check

        # Check specific edge
        infralink check --edge airflow-to-postgres

        # Check only critical edges
        infralink check --critical-only

        # Check all database edges
        infralink check --type database
    """
    from infralink.core.resolver import EdgeResolver
    from infralink.core.schema import Criticality, EdgeType

    registry = ctx.registry
    edges = ctx.edges

    resolver = EdgeResolver(registry, edges)

    # Filter edges
    edges_to_check = list(edges)

    if edge_ids:
        edges_to_check = [e for e in edges_to_check if e.id in edge_ids]

    if edge_type:
        target_type = EdgeType(edge_type)
        edges_to_check = [e for e in edges_to_check if e.type == target_type]

    if criticality:
        target_crit = Criticality(criticality)
        edges_to_check = [e for e in edges_to_check if e.criticality == target_crit]

    if critical_only:
        edges_to_check = [e for e in edges_to_check if e.is_critical]

    health_results = []
    for edge in edges_to_check:
        result = check_edge_health(edge, resolver, timeout=timeout)
        health_results.append(result)

    checks = [
        CheckResult(
            edge_id=result.edge_id,
            healthy=result.healthy,
            status=normalize_health_result(result)[0],
            latency_ms=result.latency_ms,
            error_code=normalize_health_result(result)[1],
        )
        for result in health_results
    ]
    healthy_count = sum(item.healthy for item in checks)
    selected = _active_collection(collection, cursor, ("checks",))
    result_hash = hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in checks],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    fingerprint = _topology_fingerprint(
        ctx,
        include_registry=True,
        include_edges=True,
        identifiers={
            "edge_ids": json.dumps(edge_ids),
            "edge_type": str(edge_type),
            "criticality": str(criticality),
            "critical_only": str(critical_only),
            "timeout": str(timeout),
            "results": result_hash,
        },
    )
    offset = _page_offset(
        command="check",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    command_result = CheckCommandResult(
        healthy=healthy_count == len(checks),
        checks=page_items(checks, limit=limit, offset=offset, next_cursor=None),
        summary=CheckSummary(
            total=len(checks),
            healthy=healthy_count,
            unhealthy=len(checks) - healthy_count,
        ),
    )
    _attach_next_cursors(
        command_result,
        command="check",
        collections=("checks",),
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    command_argv = ["check"]
    for edge_id in edge_ids:
        command_argv.extend(["--edge", edge_id])
    if edge_type:
        command_argv.extend(["--type", edge_type])
    if criticality:
        command_argv.extend(["--criticality", criticality])
    if critical_only:
        command_argv.append("--critical-only")
    command_argv.extend(["--timeout", str(timeout)])
    _emit_query_result(
        ctx=ctx,
        path=["check"],
        command_argv=command_argv,
        result=command_result,
        limit=limit,
    )
    return 0 if command_result.healthy else 1
