"""Main CLI entry point for infralink."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from infralink import __version__
from infralink.cli.output import ok_envelope


# Default paths (can be overridden)
DEFAULT_REGISTRY = "examples/registry.yml"
DEFAULT_EDGES = "examples/edges.yml"


def _json_out(payload: dict[str, Any]) -> None:
    """Emit JSON to stdout (agent-first / machine-friendly)."""
    console.print_json(json.dumps(payload), indent=2)


class Context:
    """CLI context object passed to commands."""

    def __init__(self) -> None:
        self.registry_path: Path | None = None
        self.edges_path: Path | None = None
        self.verbose: bool = False
        self.output: str = "json"
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

    def emit(self, payload: dict[str, Any]) -> None:
        if self.output == "json":
            _json_out(payload)
        else:
            console.print(payload.get("human", ""))


pass_context = click.make_pass_decorator(Context, ensure=True)


COMMAND_METADATA: dict[str, dict[str, str]] = {
    "analyze": {
        "description": "Analyze registry and generate derived artifacts.",
        "usage": "infralink analyze",
    },
    "check": {"description": "Run health checks for edges.", "usage": "infralink check"},
    "diagram": {
        "description": "Generate topology diagrams.",
        "usage": "infralink diagram",
    },
    "docs": {"description": "Generate documentation outputs.", "usage": "infralink docs"},
    "resolve": {"description": "Resolve an edge to targets.", "usage": "infralink resolve <edge-id>"},
    "validate": {
        "description": "Validate registry and edges.",
        "usage": "infralink validate",
    },
    "info": {"description": "Show registry and edge summary.", "usage": "infralink info"},
    "hosts": {"description": "List all hosts.", "usage": "infralink hosts"},
    "edges-list": {"description": "List all edges.", "usage": "infralink edges-list"},
}


def _load_command(name: str):
    if name == "analyze":
        from infralink.cli.analyze import analyze

        return analyze
    if name == "check":
        from infralink.cli.check import check

        return check
    if name == "diagram":
        from infralink.cli.diagram import diagram

        return diagram
    if name == "docs":
        from infralink.cli.docs import docs

        return docs
    if name == "resolve":
        from infralink.cli.resolve import resolve

        return resolve
    if name == "validate":
        from infralink.cli.validate import validate

        return validate
    if name == "info":
        return info
    if name == "hosts":
        return hosts
    if name == "edges-list":
        return edges_list
    return None


class LazyGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(COMMAND_METADATA.keys())

    def get_command(self, ctx: click.Context, cmd_name: str):
        return _load_command(cmd_name)


@click.group(cls=LazyGroup, invoke_without_command=True)
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
@click.option(
    "-o",
    "--output",
    type=click.Choice(["json", "human"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format (json = agent-first)",
)
@pass_context
def cli(ctx: Context, registry: Path, edges: Path, verbose: bool, output: str) -> None:
    """
    Infralink - Infrastructure topology modeling.

    Manage infrastructure nodes and edges for health checks,
    diagram generation, and documentation.
    """
    ctx.registry_path = registry
    ctx.edges_path = edges
    ctx.verbose = verbose
    ctx.output = output

    click_ctx = click.get_current_context()
    if click_ctx.invoked_subcommand is not None:
        return

    command_tree = []
    for name, meta in sorted(COMMAND_METADATA.items()):
        command_tree.append(
            {
                "name": name,
                "description": meta.get("description", ""),
                "usage": meta.get("usage", f"infralink {name}"),
            }
        )

    registry_summary: dict[str, Any] = {}
    edges_summary: dict[str, Any] = {}
    try:
        registry = ctx.registry
        registry_summary = {
            "path": str(ctx.registry_path),
            "total_hosts": len(registry),
            "active_hosts": len(registry.active_hosts()),
            "groups": sorted(registry.groups()),
            "clouds": sorted(registry.clouds()),
        }
    except Exception as exc:
        registry_summary = {"error": str(exc)}

    try:
        edges_obj = ctx.edges
        edges_summary = {
            "path": str(ctx.edges_path),
            "total_edges": len(edges_obj),
            "critical_edges": len(edges_obj.critical_edges()),
        }
    except Exception as exc:
        edges_summary = {"error": str(exc)}

    payload = ok_envelope(
        "infralink",
        {
            "description": "Infralink - Infrastructure topology modeling.",
            "version": __version__,
            "registry": registry_summary,
            "edges": edges_summary,
            "commands": command_tree,
        },
        [
            {"command": "infralink validate", "description": "Validate registry and edges"},
            {"command": "infralink analyze", "description": "Analyze topology coverage"},
            {"command": "infralink edges-list", "description": "List all edges"},
        ],
    )
    click.echo(json.dumps(payload))


def _links(command: str, ctx: Context) -> dict[str, str]:
    base = "infralink"
    return {
        "self": f"{base} {command}",
        "registry": str(ctx.registry_path) if ctx.registry_path else "",
        "edges": str(ctx.edges_path) if ctx.edges_path else "",
    }


@cli.command()
@pass_context
def info(ctx: Context) -> None:
    """Show registry and edge summary."""
    from infralink.core.schema import EdgeType

    try:
        registry = ctx.registry
        edges = ctx.edges
    except click.ClickException as e:
        _json_out({"status": "error", "error": str(e), "links": _links("info", ctx)})
        raise SystemExit(1)

    if ctx.output == "json":
        data = {
            "version": __version__,
            "registry": {
                "path": str(ctx.registry_path),
                "total_hosts": len(registry),
                "active_hosts": len(registry.active_hosts()),
                "groups": sorted(registry.groups()),
                "clouds": sorted(registry.clouds()),
            },
            "edges": {
                "path": str(ctx.edges_path),
                "total_edges": len(edges),
                "critical_edges": len(edges.critical_edges()),
                "by_type": {etype.value: len(edges.by_type(etype)) for etype in EdgeType},
            },
            "links": _links("info", ctx),
            "status": "ok",
        }
        _json_out(data)
        return

    # Human output
    from rich.table import Table

    console.print(f"\n[bold]Infralink v{__version__}[/bold]\n")
    console.print("[bold cyan]Registry Summary[/bold cyan]")
    console.print(f"  Path: {ctx.registry_path}")
    console.print(f"  Total hosts: {len(registry)}")
    console.print(f"  Active hosts: {len(registry.active_hosts())}")
    console.print(f"  Groups: {', '.join(sorted(registry.groups()))}")
    console.print(f"  Clouds: {', '.join(sorted(registry.clouds()))}")

    console.print(f"\n[bold cyan]Edge Summary[/bold cyan]")
    console.print(f"  Path: {ctx.edges_path}")
    console.print(f"  Total edges: {len(edges)}")
    console.print(f"  Critical edges: {len(edges.critical_edges())}")

    if len(edges) > 0:
        table = Table(title="Edges by Type")
        table.add_column("Type", style="cyan")
        table.add_column("Count", justify="right")
        for etype in EdgeType:
            count = len(edges.by_type(etype))
            if count > 0:
                table.add_row(etype.value, str(count))
        console.print(table)


@cli.command()
@pass_context
def hosts(ctx: Context) -> None:
    """List all hosts in registry."""
    try:
        registry = ctx.registry
    except click.ClickException as e:
        _json_out({"status": "error", "error": str(e), "links": _links("hosts", ctx)})
        raise SystemExit(1)

    if ctx.output == "json":
        data = [
            {
                "canonical_name": h.canonical_name,
                "uuid": h.uuid,
                "status": h.status.value,
                "group": h.group,
                "cloud": h.cloud,
                "tailscale_ip": h.tailscale_ip,
                "tailscale_name": h.tailscale_name,
            }
            for h in sorted(registry, key=lambda h: h.canonical_name)
        ]
        _json_out({"status": "ok", "hosts": data, "links": _links("hosts", ctx)})
        return

    from rich.table import Table

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


@cli.command(name="edges")
@pass_context
def edges_list(ctx: Context) -> None:
    """List all declared edges."""
    try:
        edges = ctx.edges
    except click.ClickException as e:
        _json_out({"status": "error", "error": str(e), "links": _links("edges", ctx)})
        raise SystemExit(1)

    if ctx.output == "json":
        data = [
            {
                "id": edge.id,
                "type": edge.type.value,
                "from": {
                    "hosts": edge.source_hosts,
                    "service": edge.source_service,
                },
                "to": {
                    "host": edge.target_host,
                    "service": edge.target_service,
                    "port": edge.target_port,
                },
                "protocol": edge.protocol,
                "criticality": edge.criticality.value,
            }
            for edge in edges
        ]
        _json_out({"status": "ok", "edges": data, "links": _links("edges", ctx)})
        return

    from rich.table import Table

    table = Table(title="Infrastructure Edges")
    table.add_column("ID", style="dim")
    table.add_column("Type")
    table.add_column("From")
    table.add_column("To")
    table.add_column("Protocol")

    for edge in edges:
        table.add_row(
            edge.id,
            edge.type.value,
            f"{','.join(edge.source_hosts) or '*'}:{edge.source_service or '*'}",
            f"{edge.target_host}:{edge.target_service}:{edge.target_port}",
            edge.protocol or "-",
        )

    console.print(table)


# Import commands after definitions to register
