"""Validation CLI command."""

from __future__ import annotations

from typing import Any

import click

from infralink.cli.actions import action
from infralink.cli.contracts import (
    Diagnostic,
    Page,
    PageInfo,
    ValidateResult,
    ValidationSummary,
)
from infralink.cli.errors import CliFailure

try:
    from infralink.cli.main import Context, _context_for, _emit, pass_context
except ModuleNotFoundError:
    Context = object  # type: ignore[misc,assignment]

    def pass_context(func: Any) -> Any:
        return func


from infralink.cli.output import ok_envelope


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
def validate(ctx: Context, strict: bool, check_resolution: bool) -> int:
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

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    def error(code: str, message: str, path: str | None = None) -> None:
        errors.append(Diagnostic(code=code, path=path, message=message, severity="error"))

    def warning(code: str, message: str, path: str | None = None) -> None:
        warnings.append(Diagnostic(code=code, path=path, message=message, severity="warning"))

    # Load and validate registry
    try:
        registry = ctx.registry
    except CliFailure:
        raise
    except Exception:
        error("registry_invalid", "Registry validation failed")

    # Load and validate edges
    try:
        edges = ctx.edges
    except CliFailure:
        raise
    except Exception:
        error("edges_invalid", "Edge validation failed")

    # Check edge target references
    if "registry" in dir() and "edges" in dir() and len(edges) > 0:
        for edge in edges:
            node_type_error = _edge_node_type_error(edge.source_service)
            if node_type_error:
                error("edge_source_service_invalid", node_type_error, edge.id)

            # Check target host exists
            target = registry.get_by_uuid(edge.target_host)
            if not target:
                error("target_host_not_found", "Target host not found", edge.id)
            elif not target.is_active:
                warning("target_host_inactive", "Target host is not active", edge.id)

            # Check source hosts exist
            for source_uuid in edge.source_hosts:
                source = registry.get_by_uuid(source_uuid)
                if not source:
                    error("source_host_not_found", "Source host not found", edge.id)

    # Check edge resolution
    if check_resolution and "registry" in dir() and "edges" in dir():
        resolver = EdgeResolver(registry, edges)
        resolution_errors, resolution_warnings = resolver.validate_all()
        for item in resolution_warnings:
            warning("resolution_warning", item)
        for item in resolution_errors:
            error("resolution_error", item)

    # Check for duplicate edge IDs
    if "edges" in dir():
        seen_ids: set[str] = set()
        for edge in edges:
            if edge.id in seen_ids:
                error("duplicate_edge_id", "Duplicate edge ID", edge.id)
            seen_ids.add(edge.id)

    # Check that hosts use roles, not direct services
    if "registry" in dir():
        hosts_without_roles = []
        for host in registry:
            if not host.is_active:
                continue
            # Check if host has managed_services but no roles
            if not host.roles and host.managed_services:
                hosts_without_roles.append(host)
                error(
                    "host_services_without_roles",
                    "Host has managed services but no roles",
                    host.uuid,
                )
        # Report on unmanaged infrastructure
        hosts_with_unmanaged = []
        for host in registry:
            if not host.is_active:
                continue
            if host.unmanaged_services or host.unmanaged_roles:
                hosts_with_unmanaged.append(host)
        if hosts_with_unmanaged:
            for h in hosts_with_unmanaged:
                warning(
                    "host_has_unmanaged_resources",
                    "Host has unmanaged resources",
                    h.uuid,
                )

    valid = not errors and (not strict or not warnings)
    result = ValidateResult(
        valid=valid,
        errors=Page[Diagnostic](
            items=errors[:100],
            page=PageInfo(
                limit=100,
                returned=min(len(errors), 100),
                total=len(errors),
                next_cursor=None,
            ),
        ),
        warnings=Page[Diagnostic](
            items=warnings[:100],
            page=PageInfo(
                limit=100,
                returned=min(len(warnings), 100),
                total=len(warnings),
                next_cursor=None,
            ),
        ),
        summary=ValidationSummary(error_count=len(errors), warning_count=len(warnings)),
    )
    _emit(
        ok_envelope(
            _context_for(path=["validate"]),
            result,
            [
                action("check", ["infralink", "check"], "Run edge health checks"),
                action("help", ["infralink", "help", "validate"], "Show validation help"),
            ],
        )
    )
    return 0 if valid else 1
