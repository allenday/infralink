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

    # Check edge target references
    if "registry" in dir() and "edges" in dir() and len(edges) > 0:
        console.print("[bold]Checking edge references...[/bold]")
        for edge in edges:
            # Check target host exists
            target = registry.get_by_uuid(edge.target_host)
            if not target:
                errors.append(f"Edge '{edge.id}': target host not found: {edge.target_host}")
                console.print(f"  [red]✗[/red] Edge '{edge.id}': target host not found")
            elif not target.is_active:
                warnings.append(f"Edge '{edge.id}': target host is not active: {target.canonical_name}")
                console.print(f"  [yellow]![/yellow] Edge '{edge.id}': target host not active")

            # Check source hosts exist
            for source_uuid in edge.source_hosts:
                source = registry.get_by_uuid(source_uuid)
                if not source:
                    errors.append(f"Edge '{edge.id}': source host not found: {source_uuid}")
                    console.print(f"  [red]✗[/red] Edge '{edge.id}': source not found: {source_uuid[:8]}...")

        if not errors:
            console.print(f"  [green]✓[/green] All edge references valid")

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
