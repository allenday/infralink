"""Health check CLI command."""

from __future__ import annotations

import click

from infralink.cli.main import Context, _emit, pass_context
from infralink.cli.output import error_envelope, ok_envelope


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
@pass_context
def check(
    ctx: Context,
    edge_ids: tuple[str, ...],
    edge_type: str | None,
    criticality: str | None,
    critical_only: bool,
    timeout: int,
) -> None:
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
    from infralink.health.checks import check_edge_health

    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
        edges = ctx.edges
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "CHECK_FAILED",
            "Ensure registry/edges paths are correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        _emit(payload)
        raise SystemExit(1) from exc

    if len(edges) == 0:
        payload = ok_envelope(command, {"results": [], "summary": {"healthy": 0, "failed": 0}}, [])
        _emit(payload)
        return

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

    if not edges_to_check:
        payload = ok_envelope(command, {"results": [], "summary": {"healthy": 0, "failed": 0}}, [])
        _emit(payload)
        return

    # Run health checks
    results = []
    for edge in edges_to_check:
        result = check_edge_health(edge, resolver, timeout=timeout)
        results.append(result)

    # Output results
    healthy_count = 0
    failed_count = 0
    critical_failures = 0
    results_payload = []

    for result in results:
        if result.healthy:
            healthy_count += 1
        else:
            failed_count += 1
            if result.criticality == "critical":
                critical_failures += 1

        results_payload.append(result.to_dict())

    summary = {
        "healthy": healthy_count,
        "failed": failed_count,
        "critical_failed": critical_failures,
    }

    if failed_count > 0:
        payload = error_envelope(
            command,
            "One or more edges failed health checks",
            "CHECK_FAILED",
            "Inspect failing edges and fix connectivity or credentials.",
            [
                {"command": "infralink validate", "description": "Validate registry and edges"},
                {"command": "infralink resolve <edge-id>", "description": "Resolve a failing edge"},
            ],
        )
        payload["result"] = {"results": results_payload, "summary": summary}
        _emit(payload)
        raise SystemExit(2 if critical_failures else 1)

    payload = ok_envelope(
        command,
        {"results": results_payload, "summary": summary},
        [{"command": "infralink analyze", "description": "Analyze topology coverage"}],
    )
    _emit(payload)
