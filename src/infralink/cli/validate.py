"""Validation CLI command."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import click

from infralink.cli.contracts import (
    Diagnostic,
    ValidateResult,
    ValidationSummary,
)
from infralink.cli.pagination import page_items

try:
    from infralink.cli.main import (
        Context,
        _active_collection,
        _attach_next_cursors,
        _emit_query_result,
        _page_offset,
        _page_options,
        _topology_fingerprint,
        pass_context,
    )
except ModuleNotFoundError:
    Context = object  # type: ignore[misc,assignment]

    def pass_context(func: Any) -> Any:
        return func


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
@click.option("--source", type=click.Path(path_type=Path), default=None)
@click.option("--as-of", default=None)
@click.option("--registry-revision", default=None)
@_page_options
@pass_context
def validate(
    ctx: Context,
    strict: bool,
    check_resolution: bool,
    source: Any,
    as_of: str | None,
    registry_revision: str | None,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> int:
    """
    Validate registry and edge declarations.

    Checks for schema compliance, missing references, and consistency.

    Examples:

        # Basic validation
        infralink validate

        # Strict validation with resolution checks
        infralink validate --strict --check-resolution
    """
    if source is not None:
        if as_of is None:
            raise click.UsageError("--as-of is required with --source")
        from infralink.cli.observation import run_validate

        return run_validate(ctx, source, as_of, registry_revision)

    from infralink.core.resolver import EdgeResolver

    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []

    def error(code: str, message: str, path: str | None = None) -> None:
        errors.append(Diagnostic(code=code, path=path, message=message, severity="error"))

    def warning(code: str, message: str, path: str | None = None) -> None:
        warnings.append(Diagnostic(code=code, path=path, message=message, severity="warning"))

    registry = ctx.registry
    edges = ctx.edges

    # Check edge target references
    if len(edges) > 0:
        for edge in edges:
            node_type_error = _edge_node_type_error(edge.source_service)
            if node_type_error:
                error(
                    "edge_source_service_invalid",
                    node_type_error,
                    f"edges.{edge.id}.from.service",
                )

            # Check target host exists
            target = registry.get_by_uuid(edge.target_host)
            if not target:
                error(
                    "target_host_not_found",
                    "Target host not found",
                    f"edges.{edge.id}.to.host",
                )
            elif not target.is_active:
                warning(
                    "target_host_inactive",
                    "Target host is not active",
                    f"edges.{edge.id}.to.host",
                )

            # Check source hosts exist
            for source_uuid in edge.source_hosts:
                source = registry.get_by_uuid(source_uuid)
                if not source:
                    error(
                        "source_host_not_found",
                        "Source host not found",
                        f"edges.{edge.id}.from.hosts",
                    )

    # Check edge resolution
    if check_resolution:
        resolver = EdgeResolver(registry, edges)
        resolution_errors, resolution_warnings = resolver.validate_all()
        for _item in resolution_warnings:
            warning("resolution_warning", "Resolution warning")
        for _item in resolution_errors:
            error("resolution_error", "Resolution failed")

    # Check for duplicate edge IDs
    seen_ids: set[str] = set()
    for edge in edges:
        if edge.id in seen_ids:
            error("duplicate_edge_id", "Duplicate edge ID", f"edges.{edge.id}.id")
        seen_ids.add(edge.id)

    # Check that hosts use roles, not direct services
    for host in registry:
        if not host.is_active:
            continue
        if not host.roles and host.managed_services:
            error(
                "host_services_without_roles",
                "Host has managed services but no roles",
                f"hosts.{host.uuid}.roles",
            )
        if host.unmanaged_services or host.unmanaged_roles:
            warning(
                "host_has_unmanaged_resources",
                "Host has unmanaged resources",
                f"hosts.{host.uuid}",
            )

    valid = not errors and (not strict or not warnings)
    collections = ("errors", "warnings")
    selected = _active_collection(collection, cursor, collections)
    diagnostics_hash = hashlib.sha256(
        json.dumps(
            [item.model_dump(mode="json") for item in [*errors, *warnings]],
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    fingerprint = _topology_fingerprint(
        ctx,
        include_registry=True,
        include_edges=True,
        identifiers={
            "strict": str(strict),
            "check_resolution": str(check_resolution),
            "diagnostics": diagnostics_hash,
        },
    )
    offset = _page_offset(
        command="validate",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = ValidateResult(
        valid=valid,
        errors=page_items(
            errors,
            limit=limit,
            offset=offset if selected == "errors" else 0,
            next_cursor=None,
        ),
        warnings=page_items(
            warnings,
            limit=limit,
            offset=offset if selected == "warnings" else 0,
            next_cursor=None,
        ),
        summary=ValidationSummary(error_count=len(errors), warning_count=len(warnings)),
    )
    _attach_next_cursors(
        result,
        command="validate",
        collections=collections,
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    command_argv = ["validate"]
    if strict:
        command_argv.append("--strict")
    if check_resolution:
        command_argv.append("--check-resolution")
    _emit_query_result(
        ctx=ctx,
        path=["validate"],
        command_argv=command_argv,
        result=result,
        limit=limit,
    )
    return 0 if valid else 1
