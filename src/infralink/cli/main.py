"""Main CLI entry point for infralink."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click

from infralink import __version__
from infralink.cli.output import error_envelope, ok_envelope


# Default paths (can be overridden)
DEFAULT_REGISTRY = "examples/registry.yml"
DEFAULT_EDGES = "examples/edges.yml"


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
                if self.registry_path.is_dir():
                    self._registry = Registry.load_dir(self.registry_path)
                else:
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
                from infralink.core.edges import EdgeSet

                if self.registry_path and self.registry_path.exists():
                    if self.registry_path.is_dir():
                        # For directory-based registry, we should probably look for a unified edges file
                        # or just rely on EdgeSet initialization from the already-loaded registry.
                        # Actually, EdgeSet.from_registry expects a dict.
                        # I will check if registry is already loaded.
                        if self._registry:
                             # This is tricky because self._registry is a Registry object, not a dict.
                             # But Registry has an applications property, etc.
                             # For now, if it is a directory, we just default to empty if edges.yml is missing.
                             self._edges = EdgeSet([])
                        else:
                             self._edges = EdgeSet([])
                    else:
                        with self.registry_path.open() as f:
                            data = yaml.safe_load(f)
                        self._edges = EdgeSet.from_registry(data)
                else:
                    self._edges = EdgeSet([])
        return self._edges


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
    "app": {"description": "Manage applications.", "usage": "infralink app [list|show]"},
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
    if name == "app":
        from infralink.cli.app import app

        return app
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
    type=click.Choice(["json"], case_sensitive=False),
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


@cli.command()
@pass_context
def info(ctx: Context) -> None:
    """Show registry and edge summary."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
        edges = ctx.edges
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "INFO_FAILED",
            "Ensure registry/edges paths are correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        click.echo(json.dumps(payload))
        raise SystemExit(1)

    from infralink.core.schema import EdgeType

    edge_types = []
    for etype in EdgeType:
        count = len(edges.by_type(etype))
        if count > 0:
            edge_types.append({"type": etype.value, "count": count})

    result = {
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
            "by_type": edge_types,
        },
    }
    payload = ok_envelope(
        command,
        result,
        [
            {"command": "infralink hosts", "description": "List all hosts"},
            {"command": "infralink edges-list", "description": "List all edges"},
        ],
    )
    click.echo(json.dumps(payload))


@cli.command()
@pass_context
def hosts(ctx: Context) -> None:
    """List all hosts in registry."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "HOSTS_FAILED",
            "Ensure registry path is correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        click.echo(json.dumps(payload))
        raise SystemExit(1)

    hosts_payload = []
    for host in sorted(registry, key=lambda h: h.canonical_name):
        hosts_payload.append(
            {
                "name": host.canonical_name,
                "uuid": host.uuid,
                "status": host.status.value,
                "group": host.group,
                "cloud": host.cloud,
                "tailscale_ip": host.tailscale_ip,
            }
        )

    result = {"hosts": hosts_payload, "count": len(hosts_payload)}
    payload = ok_envelope(
        command,
        result,
        [
            {"command": "infralink info", "description": "Show registry summary"},
            {"command": "infralink edges-list", "description": "List all edges"},
        ],
    )
    payload.update(result)
    click.echo(json.dumps(payload))


@cli.command()
@pass_context
def edges_list(ctx: Context) -> None:
    """List all declared edges."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        edges = ctx.edges
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "EDGES_FAILED",
            "Ensure edges path is correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        click.echo(json.dumps(payload))
        raise SystemExit(1)

    edge_payload = []
    for edge in edges:
        sources = len(edge.source_hosts) if not edge.is_wildcard_source() else "*"
        edge_payload.append(
            {
                "id": edge.id,
                "type": edge.type.value,
                "target_service": edge.target_service,
                "target_port": edge.target_port,
                "criticality": edge.criticality.value,
                "sources": sources,
            }
        )

    payload = ok_envelope(
        command,
        {"edges": edge_payload, "count": len(edge_payload)},
        [
            {"command": "infralink info", "description": "Show registry summary"},
            {"command": "infralink resolve <edge-id>", "description": "Resolve an edge"},
        ],
    )
    click.echo(json.dumps(payload))


if __name__ == "__main__":
    cli()
