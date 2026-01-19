"""Edge resolution CLI command."""

from __future__ import annotations

import click
from rich.console import Console

from infralink.cli.main import Context, pass_context

console = Console()


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
    import json
    import os

    from infralink.core.resolver import EdgeResolver, ResolutionError

    try:
        registry = ctx.registry
        edges = ctx.edges
    except click.ClickException as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    resolver = EdgeResolver(registry, edges)

    # Get password from environment if specified
    if password_env:
        password = os.environ.get(password_env)
        if not password:
            console.print(f"[red]Error:[/red] Environment variable not set: {password_env}")
            raise SystemExit(1)

    try:
        edge = resolver.get_edge(edge_id)
        target_host = resolver.get_target_host(edge_id)

        if output_format == "ip":
            console.print(resolver.get_target_ip(edge_id, prefer_ip))

        elif output_format == "endpoint":
            console.print(resolver.get_target_endpoint(edge_id, prefer_ip))

        elif output_format == "url":
            url = resolver.get_url(
                edge_id,
                user=user,
                password=password,
                database=database,
                prefer_ip=prefer_ip,
            )
            console.print(url)

        elif output_format == "json":
            data = {
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
            console.print(json.dumps(data, indent=2))

        elif output_format == "env":
            prefix = edge_id.upper().replace("-", "_")
            ip = resolver.get_target_ip(edge_id, prefer_ip)
            port = edge.target_port
            console.print(f"export {prefix}_HOST={ip}")
            console.print(f"export {prefix}_PORT={port}")
            console.print(f"export {prefix}_ENDPOINT={ip}:{port}")
            if user:
                console.print(f"export {prefix}_USER={user}")
            if database:
                console.print(f"export {prefix}_DATABASE={database}")

    except ResolutionError as e:
        console.print(f"[red]Resolution error:[/red] {e}")
        raise SystemExit(1)
