"""Edge resolution CLI command."""

from __future__ import annotations

import json
import os

import click

from infralink.cli.main import Context, pass_context
from infralink.cli.output import error_envelope, ok_envelope


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
    "--password",
    "-p",
    help="Password for URL generation (or use --password-env)",
)
@click.option(
    "--password-env",
    help="Environment variable containing password",
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
    password: str | None,
    password_env: str | None,
    database: str | None,
    prefer_ip: str,
) -> None:
    """
    Resolve an edge to its target endpoint.

    Useful for scripts and template debugging.

    Examples:

        # Get endpoint (ip:port)
        infralink resolve airflow-to-postgres

        # Get full connection URL
        infralink resolve airflow-to-postgres --format url -u airflow -p secret -d airflow

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
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "RESOLVE_FAILED",
            "Ensure registry/edges paths are correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        click.echo(json.dumps(payload))
        raise SystemExit(1)

    resolver = EdgeResolver(registry, edges)

    # Get password from environment if specified
    if password_env:
        password = os.environ.get(password_env)
        if not password:
            payload = error_envelope(
                command,
                f"Environment variable not set: {password_env}",
                "RESOLVE_PASSWORD_ENV_MISSING",
                f"Export {password_env} and re-run.",
                [{"command": "env | grep PASSWORD", "description": "Inspect environment vars"}],
            )
            click.echo(json.dumps(payload))
            raise SystemExit(1)

    try:
        edge = resolver.get_edge(edge_id)
        target_host = resolver.get_target_host(edge_id)

        if output_format == "ip":
            result = {"ip": resolver.get_target_ip(edge_id, prefer_ip)}
        elif output_format == "endpoint":
            result = {"endpoint": resolver.get_target_endpoint(edge_id, prefer_ip)}
        elif output_format == "url":
            url = resolver.get_url(
                edge_id,
                user=user,
                password=password,
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
            env_vars = {
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
                {"command": f"infralink resolve {edge_id} --format json", "description": "Resolve as JSON"},
            ],
        )
        click.echo(json.dumps(payload))

    except ResolutionError as e:
        payload = error_envelope(
            command,
            str(e),
            "RESOLUTION_FAILED",
            "Verify edge ID and registry/edges consistency.",
            [{"command": "infralink edges-list", "description": "List all edges"}],
        )
        click.echo(json.dumps(payload))
        raise SystemExit(1)
