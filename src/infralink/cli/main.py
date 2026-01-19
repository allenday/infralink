"""Main CLI entry point for infralink."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
from rich.console import Console

from infralink import __version__

console = Console()

# Default paths (can be overridden)
DEFAULT_REGISTRY = "examples/registry.yml"
DEFAULT_EDGES = "examples/edges.yml"


class Context:
    """CLI context object passed to commands."""

    def __init__(self) -> None:
        self.registry_path: Path | None = None
        self.edges_path: Path | None = None
        self.verbose: bool = False
        self._registry: Any = None
        self._edges: Any = None

    @property
    def registry(self) -> Any:
        """Lazy-load registry."""
        if self._registry is None:
            from infralink.core.registry import Registry

            if self.registry_path and self.registry_path.exists():
                self._registry = Registry.load(self.registry_path)
            else:
                raise click.ClickException(f"Registry not found: {self.registry_path}")
        return self._registry

    @property
    def edges(self) -> Any:
        """Lazy-load edges."""
        if self._edges is None:
            from infralink.core.edges import EdgeSet

            if self.edges_path and self.edges_path.exists():
                self._edges = EdgeSet.load(self.edges_path)
            else:
                # Try loading from registry
                import yaml

                if self.registry_path and self.registry_path.exists():
                    with self.registry_path.open() as f:
                        data = yaml.safe_load(f)
                    self._edges = EdgeSet.from_registry(data)
                else:
                    self._edges = EdgeSet([])
        return self._edges


pass_context = click.make_pass_decorator(Context, ensure=True)


@click.group()
@click.version_option(version=__version__, prog_name="infralink")
@click.option(
    "-r",
    "--registry",
    type=click.Path(exists=False, path_type=Path),
    default=DEFAULT_REGISTRY,
    help="Path to registry YAML file",
)
@click.option(
    "-e",
    "--edges",
    type=click.Path(exists=False, path_type=Path),
    default=DEFAULT_EDGES,
    help="Path to edges YAML file",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@pass_context
def cli(ctx: Context, registry: Path, edges: Path, verbose: bool) -> None:
    """
    Infralink - Infrastructure topology modeling.

    Manage infrastructure nodes and edges for health checks,
    diagram generation, and documentation.
    """
    ctx.registry_path = registry
    ctx.edges_path = edges
    ctx.verbose = verbose


# Import and register subcommands
from infralink.cli.analyze import analyze
from infralink.cli.check import check
from infralink.cli.diagram import diagram
from infralink.cli.docs import docs
from infralink.cli.resolve import resolve
from infralink.cli.validate import validate

cli.add_command(analyze)
cli.add_command(check)
cli.add_command(diagram)
cli.add_command(docs)
cli.add_command(resolve)
cli.add_command(validate)


@cli.command()
@pass_context
def info(ctx: Context) -> None:
    """Show registry and edge summary."""
    from rich.table import Table

    try:
        registry = ctx.registry
        edges = ctx.edges
    except click.ClickException as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    console.print(f"\n[bold]Infralink v{__version__}[/bold]\n")

    # Registry summary
    console.print("[bold cyan]Registry Summary[/bold cyan]")
    console.print(f"  Path: {ctx.registry_path}")
    console.print(f"  Total hosts: {len(registry)}")
    console.print(f"  Active hosts: {len(registry.active_hosts())}")
    console.print(f"  Groups: {', '.join(sorted(registry.groups()))}")
    console.print(f"  Clouds: {', '.join(sorted(registry.clouds()))}")

    # Edge summary
    console.print(f"\n[bold cyan]Edge Summary[/bold cyan]")
    console.print(f"  Path: {ctx.edges_path}")
    console.print(f"  Total edges: {len(edges)}")
    console.print(f"  Critical edges: {len(edges.critical_edges())}")

    # Edge type breakdown
    if len(edges) > 0:
        table = Table(title="Edges by Type")
        table.add_column("Type", style="cyan")
        table.add_column("Count", justify="right")

        from infralink.core.schema import EdgeType

        for etype in EdgeType:
            count = len(edges.by_type(etype))
            if count > 0:
                table.add_row(etype.value, str(count))

        console.print(table)


@cli.command()
@pass_context
def hosts(ctx: Context) -> None:
    """List all hosts in registry."""
    from rich.table import Table

    try:
        registry = ctx.registry
    except click.ClickException as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    table = Table(title="Infrastructure Hosts")
    table.add_column("Name", style="cyan")
    table.add_column("UUID", style="dim")
    table.add_column("Status")
    table.add_column("Group")
    table.add_column("Cloud")
    table.add_column("Tailscale IP")

    for host in sorted(registry, key=lambda h: h.canonical_name):
        status_style = "green" if host.is_active else "red"
        table.add_row(
            host.canonical_name,
            host.uuid_prefix + "...",
            f"[{status_style}]{host.status.value}[/{status_style}]",
            host.group or "-",
            host.cloud or "-",
            host.tailscale_ip or "-",
        )

    console.print(table)


@cli.command()
@pass_context
def edges_list(ctx: Context) -> None:
    """List all declared edges."""
    from rich.table import Table

    try:
        edges = ctx.edges
    except click.ClickException as e:
        console.print(f"[red]Error:[/red] {e}")
        return

    if len(edges) == 0:
        console.print("[yellow]No edges declared[/yellow]")
        return

    table = Table(title="Infrastructure Edges")
    table.add_column("ID", style="cyan")
    table.add_column("Type")
    table.add_column("Target")
    table.add_column("Port", justify="right")
    table.add_column("Criticality")
    table.add_column("Sources")

    for edge in edges:
        crit_style = "red" if edge.is_critical else "yellow" if edge.criticality.value == "high" else "dim"
        sources = len(edge.source_hosts) if not edge.is_wildcard_source() else "*"
        table.add_row(
            edge.id,
            edge.type.value,
            edge.target_service,
            str(edge.target_port),
            f"[{crit_style}]{edge.criticality.value}[/{crit_style}]",
            str(sources),
        )

    console.print(table)


if __name__ == "__main__":
    cli()
