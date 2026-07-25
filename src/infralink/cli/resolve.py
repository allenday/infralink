"""Edge resolution CLI command."""

from __future__ import annotations

from typing import Any

import click

from infralink.cli.actions import action
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import Context, _emit, entity_not_found, pass_context
from infralink.cli.output import ok_envelope


@click.command()
@click.argument("edge_id")
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["url", "endpoint", "ip", "json", "env"]),
    default="endpoint",
    help="Output format",
)
@click.option(
    "--user",
    "-u",
    help="Username for URL generation",
)
@click.option(
    "--database",
    "-d",
    help="Database name for URL generation",
)
@click.option(
    "--prefer-ip",
    type=click.Choice(["tailscale", "public", "private"]),
    default="tailscale",
    help="Preferred IP type",
)
@pass_context
def resolve(
    ctx: Context,
    edge_id: str,
    output_format: str,
    user: str | None,
    database: str | None,
    prefer_ip: str,
) -> None:
    """
    Resolve an edge to its target endpoint.

    Useful for scripts and template debugging.

    Examples:

        # Get endpoint (ip:port)
        infralink resolve airflow-to-postgres

        # Get a connection URL without credentials
        infralink resolve airflow-to-postgres --format url -u airflow -d airflow

        # Get just the IP
        infralink resolve airflow-to-postgres --format ip

        # Output as environment variables
        infralink resolve airflow-to-postgres --format env
    """
    from infralink.core.resolver import EdgeResolver, ResolutionError

    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
        edges = ctx.edges
    except CliFailure:
        raise
    except Exception:
        raise CliFailure(
            code=ErrorCode.INTERNAL_ERROR,
            message="Resolve could not start",
            exit_code=70,
            fix="Retry the command or report the failure",
            details={},
            next_actions=[],
        ) from None

    resolver = EdgeResolver(registry, edges)
    if edges.get(edge_id) is None:
        raise entity_not_found("edge", edge_id)

    try:
        edge = resolver.get_edge(edge_id)
        target_host = resolver.get_target_host(edge_id)
        result: dict[str, Any]

        if output_format == "ip":
            result = {"ip": resolver.get_target_ip(edge_id, prefer_ip)}
        elif output_format == "endpoint":
            result = {"endpoint": resolver.get_target_endpoint(edge_id, prefer_ip)}
        elif output_format == "url":
            url = resolver.get_url(
                edge_id,
                user=user,
                database=database,
                prefer_ip=prefer_ip,
            )
            result = {"url": url}
        elif output_format == "json":
            result = {
                "edge_id": edge.id,
                "type": edge.type.value,
                "target": {
                    "host_uuid": target_host.uuid,
                    "host_name": target_host.canonical_name,
                    "service": edge.target_service,
                    "port": edge.target_port,
                    "ip": {
                        "tailscale": target_host.tailscale_ip,
                        "public": target_host.public_ip,
                    },
                },
                "protocol": edge.protocol,
                "criticality": edge.criticality.value,
            }
        elif output_format == "env":
            prefix = edge_id.upper().replace("-", "_")
            ip = resolver.get_target_ip(edge_id, prefer_ip)
            port = edge.target_port
            env_vars: dict[str, Any] = {
                f"{prefix}_HOST": ip,
                f"{prefix}_PORT": port,
                f"{prefix}_ENDPOINT": f"{ip}:{port}",
            }
            if user:
                env_vars[f"{prefix}_USER"] = user
            if database:
                env_vars[f"{prefix}_DATABASE"] = database
            result = {"env": env_vars}
        else:
            result = {}

        payload = ok_envelope(
            command,
            result,
            [
                {"command": "infralink edges-list", "description": "List all edges"},
                {
                    "command": f"infralink resolve {edge_id} --format json",
                    "description": "Resolve as JSON",
                },
            ],
        )
        _emit(payload)

    except ResolutionError:
        raise CliFailure(
            code=ErrorCode.INPUT_LOAD_FAILED,
            message="Edge could not be resolved",
            exit_code=3,
            fix="Verify the edge and its target host declarations",
            details={},
            next_actions=[action("list", ["infralink", "edges-list"], "List all edges")],
        ) from None
