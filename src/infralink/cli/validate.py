"""Validation CLI command."""

from __future__ import annotations

import json

import click

try:
    from infralink.cli.main import Context, pass_context
except ModuleNotFoundError:
    Context = object  # type: ignore[misc,assignment]

    def pass_context(func):  # type: ignore[misc]
        return func

from infralink.cli.output import error_envelope, ok_envelope


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

    errors: list[str] = []
    warnings: list[str] = []
    command = click.get_current_context().command_path.replace("cli", "infralink")

    # Load and validate registry
    try:
        registry = ctx.registry
    except Exception as e:
        errors.append(f"Registry validation failed: {e}")

    # Load and validate edges
    try:
        edges = ctx.edges
    except Exception as e:
        errors.append(f"Edge validation failed: {e}")

    # Check edge target references
    if "registry" in dir() and "edges" in dir() and len(edges) > 0:
        for edge in edges:
            node_type_error = _edge_node_type_error(edge.source_service)
            if node_type_error:
                errors.append(f"Edge '{edge.id}': {node_type_error}")

            # Check target host exists
            target = registry.get_by_uuid(edge.target_host)
            if not target:
                errors.append(f"Edge '{edge.id}': target host not found: {edge.target_host}")
            elif not target.is_active:
                warnings.append(f"Edge '{edge.id}': target host is not active: {target.canonical_name}")

            # Check source hosts exist
            for source_uuid in edge.source_hosts:
                source = registry.get_by_uuid(source_uuid)
                if not source:
                    errors.append(f"Edge '{edge.id}': source host not found: {source_uuid}")

    # Check edge resolution
    if check_resolution and "registry" in dir() and "edges" in dir():
        resolver = EdgeResolver(registry, edges)
        resolution_errors = resolver.validate_all()
        if resolution_errors:
            for err in resolution_errors:
                errors.append(err)

    # Check for duplicate edge IDs
    if "edges" in dir():
        seen_ids: set[str] = set()
        for edge in edges:
            if edge.id in seen_ids:
                errors.append(f"Duplicate edge ID: {edge.id}")
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
                errors.append(
                    f"Host '{host.canonical_name}' ({host.uuid_prefix}): "
                    f"has managed_services but no roles. Declare roles instead."
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
                unmanaged_items = list(h.unmanaged_services.keys()) + list(h.unmanaged_roles.keys())
                warnings.append(
                    f"Host '{h.canonical_name}' has unmanaged: {', '.join(unmanaged_items)}"
                )

    result = {
        "valid": len(errors) == 0 and (not strict or len(warnings) == 0),
        "errors": errors,
        "warnings": warnings,
    }

    if errors:
        payload = error_envelope(
            command,
            "Validation failed",
            "VALIDATION_FAILED",
            "Fix registry/edge errors and re-run infralink validate.",
            [
                {"command": "infralink validate", "description": "Re-run validation"},
                {"command": "infralink analyze", "description": "Inspect topology coverage"},
            ],
        )
        payload["result"] = result
        click.echo(json.dumps(payload))
        raise SystemExit(1)

    if warnings and strict:
        payload = error_envelope(
            command,
            "Validation failed (strict mode)",
            "VALIDATION_STRICT_FAILED",
            "Resolve warnings or run without --strict.",
            [
                {"command": "infralink validate", "description": "Re-run validation"},
                {"command": "infralink validate --strict", "description": "Re-run in strict mode"},
            ],
        )
        payload["result"] = result
        click.echo(json.dumps(payload))
        raise SystemExit(1)

    payload = ok_envelope(
        command,
        result,
        [
            {"command": "infralink check", "description": "Run edge health checks"},
            {"command": "infralink analyze", "description": "Analyze topology coverage"},
        ],
    )
    click.echo(json.dumps(payload))
