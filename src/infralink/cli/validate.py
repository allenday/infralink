"""Validation CLI command."""

from __future__ import annotations

import click
from rich.console import Console

from infralink.cli.main import Context, pass_context

console = Console()


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
    from infralink.core.validator import validate_edges_against_registry

    errors: list[str] = []
    warnings: list[str] = []

    # Load and validate registry
    console.print("[bold]Validating registry...[/bold]")
    try:
        registry = ctx.registry
        console.print(f"  [green]✓[/green] Registry loaded: {len(registry)} hosts")
    except Exception as e:
        errors.append(f"Registry validation failed: {e}")
        console.print(f"  [red]✗[/red] Registry validation failed: {e}")

    # Load and validate edges
    console.print("[bold]Validating edges...[/bold]")
    try:
        edges = ctx.edges
        console.print(f"  [green]✓[/green] Edges loaded: {len(edges)} edges")
    except Exception as e:
        errors.append(f"Edge validation failed: {e}")
        console.print(f"  [red]✗[/red] Edge validation failed: {e}")

    # Cross-validate edges against registry
    if "registry" in dir() and "edges" in dir() and len(edges) > 0:
        console.print("[bold]Cross-validating edges against registry...[/bold]")
        cross_result = validate_edges_against_registry(registry, edges)
        if cross_result.errors:
            for err in cross_result.errors:
                console.print(f"  [red]✗[/red] {err}")
        if cross_result.warnings:
            for warn in cross_result.warnings:
                console.print(f"  [yellow]![/yellow] {warn}")
        if not cross_result.errors and not cross_result.warnings:
            console.print(f"  [green]✓[/green] Cross-validation passed")

        errors.extend(cross_result.errors)
        warnings.extend(cross_result.warnings)

    # Check edge resolution
    if check_resolution and "registry" in dir() and "edges" in dir():
        console.print("[bold]Checking edge resolution...[/bold]")
        resolver = EdgeResolver(registry, edges)
        resolution_errors = resolver.validate_all()
        if resolution_errors:
            for err in resolution_errors:
                errors.append(err)
                console.print(f"  [red]✗[/red] {err}")
        else:
            console.print(f"  [green]✓[/green] All edges resolvable")

    # Check for duplicate edge IDs
    if "edges" in dir():
        seen_ids: set[str] = set()
        for edge in edges:
            if edge.id in seen_ids:
                errors.append(f"Duplicate edge ID: {edge.id}")
            seen_ids.add(edge.id)

    # Check that hosts use roles, not direct services
    if "registry" in dir():
        console.print("[bold]Checking role declarations...[/bold]")
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
            console.print(
                f"  [red]✗[/red] {len(hosts_without_roles)} host(s) use services without roles"
            )
        else:
            console.print(f"  [green]✓[/green] All active hosts declare roles")

        # Report on unmanaged infrastructure
        hosts_with_unmanaged = []
        for host in registry:
            if not host.is_active:
                continue
            if host.unmanaged_services or host.unmanaged_roles:
                hosts_with_unmanaged.append(host)
        if hosts_with_unmanaged:
            console.print(
                f"  [yellow]![/yellow] {len(hosts_with_unmanaged)} host(s) have unmanaged infrastructure"
            )
            for h in hosts_with_unmanaged:
                unmanaged_items = list(h.unmanaged_services.keys()) + list(h.unmanaged_roles.keys())
                warnings.append(
                    f"Host '{h.canonical_name}' has unmanaged: {', '.join(unmanaged_items)}"
                )

    # Summary
    console.print("\n[bold]Validation Summary[/bold]")
    console.print(f"  Errors: {len(errors)}")
    console.print(f"  Warnings: {len(warnings)}")

    if errors:
        console.print("\n[red bold]Validation failed[/red bold]")
        for err in errors:
            console.print(f"  [red]•[/red] {err}")
        raise SystemExit(1)

    if warnings and strict:
        console.print("\n[yellow bold]Validation failed (strict mode)[/yellow bold]")
        for warn in warnings:
            console.print(f"  [yellow]•[/yellow] {warn}")
        raise SystemExit(1)

    if warnings:
        console.print("\n[yellow]Warnings:[/yellow]")
        for warn in warnings:
            console.print(f"  [yellow]•[/yellow] {warn}")

    console.print("\n[green bold]Validation passed[/green bold]")
