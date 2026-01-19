"""Health check CLI command."""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table

from infralink.cli.main import Context, pass_context

console = Console()


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
@click.option(
    "--json",
    "output_json",
    is_flag=True,
    help="Output results as JSON",
)
@pass_context
def check(
    ctx: Context,
    edge_ids: tuple[str, ...],
    edge_type: str | None,
    criticality: str | None,
    critical_only: bool,
    timeout: int,
    output_json: bool,
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

    try:
        registry = ctx.registry
        edges = ctx.edges
    except click.ClickException as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    if len(edges) == 0:
        console.print("[yellow]No edges declared[/yellow]")
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
        console.print("[yellow]No edges match filter criteria[/yellow]")
        return

    # Run health checks
    results = []
    for edge in edges_to_check:
        result = check_edge_health(edge, resolver, timeout=timeout)
        results.append(result)

    # Output results
    if output_json:
        import json

        console.print(json.dumps([r.to_dict() for r in results], indent=2))
        return

    # Table output
    table = Table(title="Edge Health Check Results")
    table.add_column("Edge ID", style="cyan")
    table.add_column("Type")
    table.add_column("Target")
    table.add_column("Status")
    table.add_column("Latency")
    table.add_column("Message")

    healthy_count = 0
    failed_count = 0

    for result in results:
        if result.healthy:
            status = "[green]✓ healthy[/green]"
            healthy_count += 1
        else:
            status = "[red]✗ unhealthy[/red]"
            failed_count += 1

        latency = f"{result.latency_ms:.1f}ms" if result.latency_ms else "-"

        table.add_row(
            result.edge_id,
            result.edge_type,
            result.target_endpoint,
            status,
            latency,
            result.message or "",
        )

    console.print(table)

    # Summary
    console.print(f"\n[bold]Summary:[/bold] {healthy_count} healthy, {failed_count} failed")

    if failed_count > 0:
        # Check for critical failures
        critical_failures = [r for r in results if not r.healthy and r.criticality == "critical"]
        if critical_failures:
            console.print(
                f"[red bold]CRITICAL:[/red bold] {len(critical_failures)} critical edge(s) unhealthy"
            )
            raise SystemExit(2)
        raise SystemExit(1)
