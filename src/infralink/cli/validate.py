"""Validation CLI command."""

from __future__ import annotations

import click

try:
    from infralink.cli.main import Context, pass_context
except ModuleNotFoundError:
    Context = object  # type: ignore[misc,assignment]

    def pass_context(func):  # type: ignore[misc]
        return func

def _get_console():
    try:
        from rich.console import Console
    except ModuleNotFoundError:
        return None
    return Console()


def _edge_node_type_error(source_service: str | None) -> str | None:
    if source_service is None:
        return None
    if isinstance(source_service, str) and source_service.strip():
        return None
    return "Edge source_service must be a non-empty string when provided"


@click.command()
@click.option(
    "--strict",
    is_flag=True,
    help="Fail on warnings",
)
@click.option(
    "--check-resolution",
    is_flag=True,
    help="Validate all edges can be resolved",
)
@pass_context
def validate(ctx: Context, strict: bool, check_resolution: bool) -> None:
    """
    Validate registry and edge declarations.

    Checks for schema compliance, missing references, and consistency.

    Examples:

        # Basic validation
        infralink validate

        # Strict validation with resolution checks
        infralink validate --strict --check-resolution
    """
    from infralink.core.resolver import EdgeResolver

    console = _get_console()
    errors: list[str] = []
    warnings: list[str] = []

    def emit(message: str) -> None:
        if console:
            console.print(message)
        else:
            print(message)

    # Load and validate registry
    emit("[bold]Validating registry...[/bold]")
    try:
        registry = ctx.registry
        emit(f"  [green]✓[/green] Registry loaded: {len(registry)} hosts")
    except Exception as e:
        errors.append(f"Registry validation failed: {e}")
        emit(f"  [red]✗[/red] Registry validation failed: {e}")

    # Load and validate edges
    emit("[bold]Validating edges...[/bold]")
    try:
        edges = ctx.edges
        emit(f"  [green]✓[/green] Edges loaded: {len(edges)} edges")
    except Exception as e:
        errors.append(f"Edge validation failed: {e}")
        emit(f"  [red]✗[/red] Edge validation failed: {e}")

    # Check edge target references
    if "registry" in dir() and "edges" in dir() and len(edges) > 0:
        emit("[bold]Checking edge references...[/bold]")
        for edge in edges:
            node_type_error = _edge_node_type_error(edge.source_service)
            if node_type_error:
                errors.append(f"Edge '{edge.id}': {node_type_error}")
                emit(f"  [red]✗[/red] Edge '{edge.id}': {node_type_error}")

            # Check target host exists
            target = registry.get_by_uuid(edge.target_host)
            if not target:
                errors.append(f"Edge '{edge.id}': target host not found: {edge.target_host}")
                emit(f"  [red]✗[/red] Edge '{edge.id}': target host not found")
            elif not target.is_active:
                warnings.append(f"Edge '{edge.id}': target host is not active: {target.canonical_name}")
                emit(f"  [yellow]![/yellow] Edge '{edge.id}': target host not active")

            # Check source hosts exist
            for source_uuid in edge.source_hosts:
                source = registry.get_by_uuid(source_uuid)
                if not source:
                    errors.append(f"Edge '{edge.id}': source host not found: {source_uuid}")
                    emit(f"  [red]✗[/red] Edge '{edge.id}': source not found: {source_uuid[:8]}...")

        if not errors:
            emit(f"  [green]✓[/green] All edge references valid")

    # Check edge resolution
    if check_resolution and "registry" in dir() and "edges" in dir():
        emit("[bold]Checking edge resolution...[/bold]")
        resolver = EdgeResolver(registry, edges)
        resolution_errors = resolver.validate_all()
        if resolution_errors:
            for err in resolution_errors:
                errors.append(err)
                emit(f"  [red]✗[/red] {err}")
        else:
            emit(f"  [green]✓[/green] All edges resolvable")

    # Check for duplicate edge IDs
    if "edges" in dir():
        seen_ids: set[str] = set()
        for edge in edges:
            if edge.id in seen_ids:
                errors.append(f"Duplicate edge ID: {edge.id}")
            seen_ids.add(edge.id)

    # Check that hosts use roles, not direct services
    if "registry" in dir():
        emit("[bold]Checking role declarations...[/bold]")
        hosts_without_roles = []
        for host in registry:
            if not host.is_active:
                continue
            # Check if host has managed_services but no roles
            if not host.roles and host.managed_services:
                hosts_without_roles.append(host)
                errors.append(
                    f"Host '{host.canonical_name}' ({host.uuid_prefix}): "
                    f"has managed_services but no roles. Declare roles instead."
                )
        if hosts_without_roles:
            emit(
                f"  [red]✗[/red] {len(hosts_without_roles)} host(s) use services without roles"
            )
        else:
            emit(f"  [green]✓[/green] All active hosts declare roles")

        # Report on unmanaged infrastructure
        hosts_with_unmanaged = []
        for host in registry:
            if not host.is_active:
                continue
            if host.unmanaged_services or host.unmanaged_roles:
                hosts_with_unmanaged.append(host)
        if hosts_with_unmanaged:
            emit(
                f"  [yellow]![/yellow] {len(hosts_with_unmanaged)} host(s) have unmanaged infrastructure"
            )
            for h in hosts_with_unmanaged:
                unmanaged_items = list(h.unmanaged_services.keys()) + list(h.unmanaged_roles.keys())
                warnings.append(
                    f"Host '{h.canonical_name}' has unmanaged: {', '.join(unmanaged_items)}"
                )

    # Summary
    emit("\n[bold]Validation Summary[/bold]")
    emit(f"  Errors: {len(errors)}")
    emit(f"  Warnings: {len(warnings)}")

    if errors:
        emit("\n[red bold]Validation failed[/red bold]")
        for err in errors:
            emit(f"  [red]•[/red] {err}")
        raise SystemExit(1)

    if warnings and strict:
        emit("\n[yellow bold]Validation failed (strict mode)[/yellow bold]")
        for warn in warnings:
            emit(f"  [yellow]•[/yellow] {warn}")
        raise SystemExit(1)

    if warnings:
        emit("\n[yellow]Warnings:[/yellow]")
        for warn in warnings:
            emit(f"  [yellow]•[/yellow] {warn}")

    emit("\n[green bold]Validation passed[/green bold]")
