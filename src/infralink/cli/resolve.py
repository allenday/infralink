"""Edge resolution CLI command."""

from __future__ import annotations

import shlex

import click

from infralink.cli.actions import action
from infralink.cli.contracts import Endpoint, ResolveResult
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import (
    Context,
    _context_for,
    _emit,
    _root_source_argv,
    pass_context,
)
from infralink.cli.output import ok_envelope
from infralink.cli.pagination import page_items
from infralink.cli.queries import edge_summary


@click.command()
@click.argument("edge_id")
@click.option(
    "--user",
    "-u",
    help="Username for connection template generation",
)
@click.option(
    "--database",
    "-d",
    help="Database name for connection template generation",
)
@click.option(
    "--prefer-ip",
    type=click.Choice(["tailscale", "public", "private"]),
    default="tailscale",
)
@pass_context
def resolve(
    ctx: Context,
    edge_id: str,
    user: str | None,
    database: str | None,
    prefer_ip: str,
) -> None:
    """Resolve an edge to its endpoint and safe connection template."""
    from infralink.core.resolver import EdgeResolver, ResolutionError

    registry = ctx.registry
    edges = ctx.edges
    resolver = EdgeResolver(registry, edges)
    if edges.get(edge_id) is None:
        discovery = [*_root_source_argv(ctx), "edge", "list"]
        raise CliFailure(
            code=ErrorCode.ENTITY_NOT_FOUND,
            message="Edge not found",
            exit_code=3,
            fix=f"Run {shlex.join(discovery)}",
            details={"entity_type": "edge", "requested_id": edge_id},
            next_actions=[action("list", discovery, "List edge records")],
        )

    try:
        edge = resolver.get_edge(edge_id)
        result = ResolveResult(
            edge=edge_summary(edge),
            endpoint=Endpoint(
                host=resolver.get_target_ip(edge_id, prefer_ip),
                port=resolver.get_target_port(edge_id),
                protocol=edge.protocol,
            ),
            connection_template=resolver.get_connection_template(
                edge_id,
                user=user,
                database=database,
                prefer_ip=prefer_ip,
            ),
            secret_refs=page_items(
                [edge.secret_ref] if edge.secret_ref else [],
                limit=100,
                offset=0,
                next_cursor=None,
            ),
        )

    except ResolutionError:
        source = _root_source_argv(ctx)
        raise CliFailure(
            code=ErrorCode.INPUT_LOAD_FAILED,
            message="Edge could not be resolved",
            exit_code=3,
            fix="Verify the edge and its target host declarations",
            details={},
            next_actions=[
                action("list", [*source, "edge", "list"], "List all edges"),
            ],
        ) from None

    source = _root_source_argv(ctx)
    _emit(
        ok_envelope(
            _context_for(path=["resolve"]),
            result,
            [
                action(
                    "validate",
                    [*source, "validate", "--check-resolution"],
                    "Validate all edge resolution",
                ),
                action(
                    "check",
                    [*source, "check", "--edge", edge_id],
                    "Check this edge",
                ),
            ],
        )
    )
