"""Validation CLI command."""

from __future__ import annotations

import json
from typing import Any

import click


def _get_console():
    try:
        from rich.console import Console
    except ModuleNotFoundError:  # pragma: no cover
        return None
    return Console()


def _edge_node_type_error(source_service: str | None) -> str | None:
    if source_service is None:
        return None
    if isinstance(source_service, str) and source_service.strip():
        return None
    return "Edge source_service must be a non-empty string when provided"


def _json_out(payload: dict) -> None:
    print(json.dumps(payload, indent=2))


def _links(command: str, ctx: Any) -> dict[str, str]:
    base = "infralink"
    return {
        "self": f"{base} {command}",
        "registry": str(getattr(ctx, "registry_path", "")),
        "edges": str(getattr(ctx, "edges_path", "")),
    }


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
@click.pass_obj
def validate(ctx: Any, strict: bool, check_resolution: bool) -> None:
    """
    Validate registry and edge declarations.

    Checks for schema compliance, missing references, and consistency.
    """
    from infralink.core.resolver import EdgeResolver

    console = _get_console()
    errors: list[str] = []
    warnings: list[str] = []
    json_mode = getattr(ctx, "output", "json") == "json"

    def emit(message: str) -> None:
        if json_mode:
            return
        if console:
            console.print(message)
        else:
            print(message)

    # Load and validate registry
    emit("[bold]Validating registry...[/bold]")
    try:
        registry = ctx.registry
        emit(f"  [green]\u2713[/green] Registry loaded: {len(registry)} hosts")
    except Exception as e:  # pragma: no cover - exercised in CLI tests
        errors.append(f"Registry validation failed: {e}")
        emit(f"  [red]\u2717[/red] Registry validation failed: {e}")

    # Load and validate edges
    emit("[bold]Validating edges...[/bold]")
    try:
        edges = ctx.edges
        emit(f"  [green]\u2713[/green] Edges loaded: {len(edges)} edges")
    except Exception as e:  # pragma: no cover
        errors.append(f"Edge validation failed: {e}")
        emit(f"  [red]\u2717[/red] Edge validation failed: {e}")

    # Check edge target references
    if "registry" in locals() and "edges" in locals() and len(edges) > 0:
        emit("[bold]Checking edge references...[/bold]")
        for edge in edges:
            node_type_error = _edge_node_type_error(edge.source_service)
            if node_type_error:
                errors.append(f"Edge '{edge.id}': {node_type_error}")
                emit(f"  [red]\u2717[/red] Edge '{edge.id}': {node_type_error}")

            target = registry.get_by_uuid(edge.target_host)
            if not target:
                errors.append(f"Edge '{edge.id}': target host not found: {edge.target_host}")
                emit(f"  [red]\u2717[/red] Edge '{edge.id}': target host not found")
            elif not target.is_active:
                warnings.append(
                    f"Edge '{edge.id}': target host is not active: {target.canonical_name}"
                )
                emit(f"  [yellow]![/yellow] Edge '{edge.id}': target host not active")

            for source_uuid in edge.source_hosts:
                source = registry.get_by_uuid(source_uuid)
                if not source:
                    errors.append(f"Edge '{edge.id}': source host not found: {source_uuid}")
                    emit(
                        f"  [red]\u2717[/red] Edge '{edge.id}': source not found: {source_uuid[:8]}..."
                    )

        if not errors:
            emit(f"  [green]\u2713[/green] All edge references valid")

    if check_resolution and "registry" in locals() and "edges" in locals():
        emit("[bold]Checking edge resolution...[/bold]")
        resolver = EdgeResolver(registry, edges)
        resolution_errors = resolver.validate_all()
        if resolution_errors:
            for err in resolution_errors:
                errors.append(err)
                emit(f"  [red]\u2717[/red] {err}")
        else:
            emit(f"  [green]\u2713[/green] All edges resolvable")

    if "edges" in locals():
        seen_ids: set[str] = set()
        for edge in edges:
            if edge.id in seen_ids:
                errors.append(f"Duplicate edge ID: {edge.id}")
            seen_ids.add(edge.id)

    if "registry" in locals():
        emit("[bold]Checking role declarations...[/bold]")
        hosts_without_roles = []
        for host in registry:
            if not host.is_active:
                continue
            if not host.roles and host.managed_services:
                hosts_without_roles.append(host)
                errors.append(
                    f"Host '{host.canonical_name}' ({host.uuid_prefix}): has managed_services but no roles. Declare roles instead."
                )
        if hosts_without_roles:
            emit(f"  [red]\u2717[/red] {len(hosts_without_roles)} host(s) use services without roles")
        else:
            emit(f"  [green]\u2713[/green] All active hosts declare roles")

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

    summary = {"errors": len(errors), "warnings": len(warnings)}

    if json_mode:
        status = "ok"
        exit_code = 0
        if errors:
            status = "error"
            exit_code = 1
        elif warnings and strict:
            status = "error"
            exit_code = 1
        elif warnings:
            status = "warn"
        payload = {
            "status": status,
            "summary": summary,
            "errors": errors,
            "warnings": warnings,
            "links": _links("validate", ctx),
        }
        _json_out(payload)
        raise SystemExit(exit_code)

    emit("\n[bold]Validation Summary[/bold]")
    emit(f"  Errors: {len(errors)}")
    emit(f"  Warnings: {len(warnings)}")

    if errors:
        emit("\n[red bold]Validation failed[/red bold]")
        for err in errors:
            emit(f"  [red]\u2022[/red] {err}")
        raise SystemExit(1)

    if warnings and strict:
        emit("\n[yellow bold]Validation failed (strict mode)[/yellow bold]")
        for warn in warnings:
            emit(f"  [yellow]\u2022[/yellow] {warn}")
        raise SystemExit(1)

    if warnings:
        emit("\n[yellow]Warnings:[/yellow]")
        for warn in warnings:
            emit(f"  [yellow]\u2022[/yellow] {warn}")

    emit("\n[green bold]Validation passed[/green bold]")
