"""Read-only declared-topology and direct edge diagnostics."""

from __future__ import annotations

from typing import Literal

import click

from infralink.cli.actions import action
from infralink.cli.contracts import DoctorCheck, DoctorResult, DoctorTarget
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.main import Context, _context_for, _emit, _root_source_argv, pass_context
from infralink.cli.output import ok_envelope
from infralink.health.checks import check_edge_health, normalize_health_result

DoctorKind = Literal["host", "service", "edge", "profile"]


def _missing(kind: DoctorKind, ref: str) -> CliFailure:
    collection = "host" if kind == "host" else "service"
    return CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message=f"{kind.title()} not found",
        exit_code=ExitCode.INPUT_ERROR,
        fix=f"Run infralink {collection} list",
        details={"entity_type": kind, "requested_id": ref},
        next_actions=[
            action(
                "list",
                ["infralink", collection, "list"],
                f"List {collection} records",
            )
        ],
    )


def _status(checks: list[DoctorCheck]) -> tuple[str, str | None]:
    if not checks:
        return "unknown", "no_observation_evidence"
    statuses = {item.status for item in checks}
    if "unavailable" in statuses:
        return "unavailable", None
    if "unhealthy" in statuses:
        return "unhealthy", None
    if "unknown" in statuses:
        return "unknown", "no_observation_evidence"
    return "healthy", None


def _checks(ctx: Context, edges: list[object], timeout: int) -> list[DoctorCheck]:
    from infralink.core.resolver import EdgeResolver

    resolver = EdgeResolver(ctx.registry, ctx.edges)
    values: list[DoctorCheck] = []
    for edge in sorted(edges, key=lambda item: item.id):
        observation = check_edge_health(edge, resolver, timeout=timeout)
        status, error_code = normalize_health_result(observation)
        values.append(
            DoctorCheck(
                edge_id=edge.id,
                status=status,
                latency_ms=observation.latency_ms,
                error_code=error_code,
            )
        )
    return values


def _emit_result(
    ctx: Context, result: DoctorResult, path: list[str], actions: list[object]
) -> None:
    _emit(ok_envelope(_context_for(path=path), result, actions))


@click.command(name="doctor")
@click.argument(
    "target_type", required=False, type=click.Choice(["host", "service", "edge", "profile"])
)
@click.argument("target_ref", required=False)
@click.option("--timeout", type=click.IntRange(1, 60), default=5, show_default=True)
@pass_context
def doctor(
    ctx: Context,
    target_type: DoctorKind | None,
    target_ref: str | None,
    timeout: int,
) -> int:
    """Inspect declared topology and collect direct read-only edge evidence."""
    if target_type is None:
        if target_ref is not None:
            raise click.UsageError("a target type is required")
        from infralink.cli.queries import list_services

        result = DoctorResult(
            target=DoctorTarget(type="global"),
            declared={
                "host_count": len(ctx.registry),
                "service_count": list_services(ctx.registry, ctx.edges, limit=1).page.total,
                "edge_count": len(ctx.edges),
            },
            checks=[],
            status="unknown",
            reason="no_observation_evidence",
        )
        _emit_result(
            ctx,
            result,
            ["doctor"],
            [
                action("help", ["infralink", "help", "doctor"], "Show doctor usage"),
                action("list", [*_root_source_argv(ctx), "host", "list"], "List hosts"),
            ],
        )
        return 0

    if target_ref is None:
        raise click.UsageError("a target reference is required")

    if target_type == "host":
        host = ctx.registry.get(target_ref)
        if host is None:
            raise _missing("host", target_ref)
        related = [
            edge
            for edge in ctx.edges
            if edge.target_host == host.uuid or edge.matches_source(host.uuid)
        ]
        checks = _checks(ctx, related, timeout)
        status, reason = _status(checks)
        result = DoctorResult(
            target=DoctorTarget(type="host", id=host.uuid, canonical_name=host.canonical_name),
            declared={
                "status": host.status.value,
                "services": sorted(host.service_names),
                "incoming_edge_count": len(ctx.edges.targeting_host(host.uuid)),
                "outgoing_edge_count": len(ctx.edges.from_host(host.uuid)),
            },
            checks=checks,
            status=status,
            reason=reason,
        )
        actions = [
            action("show", [*_root_source_argv(ctx), "host", "show", host.uuid], "Show host"),
        ]
    elif target_type == "edge":
        edge = ctx.edges.get(target_ref)
        if edge is None:
            raise _missing("edge", target_ref)
        checks = _checks(ctx, [edge], timeout)
        status, reason = _status(checks)
        result = DoctorResult(
            target=DoctorTarget(type="edge", id=edge.id),
            declared={
                "target_host": edge.target_host,
                "target_service": edge.target_service,
                "port": edge.declared_target_port,
                "protocol": edge.protocol,
            },
            checks=checks,
            status=status,
            reason=reason,
        )
        actions = [
            action("show", [*_root_source_argv(ctx), "edge", "show", edge.id], "Show edge"),
            action("check", [*_root_source_argv(ctx), "check", "--edge", edge.id], "Check edge"),
        ]
    else:
        service_ids = {
            service_id
            for host in ctx.registry
            for service_id in set(host.service_names) | set(host.roles)
        }
        service_ids.update(edge.target_service for edge in ctx.edges)
        if target_ref not in service_ids:
            raise _missing(target_type, target_ref)
        related = [
            edge
            for edge in ctx.edges
            if edge.target_service == target_ref or edge.source_service == target_ref
        ]
        hosts = sorted(
            host.uuid
            for host in ctx.registry
            if target_ref in set(host.service_names) | set(host.roles)
        )
        checks = _checks(ctx, related, timeout)
        status, reason = _status(checks)
        result = DoctorResult(
            target=DoctorTarget(type=target_type, id=target_ref),
            declared={"host_ids": hosts, "edge_count": len(related)},
            checks=checks,
            status=status,
            reason=reason,
        )
        actions = [
            action(
                "show",
                [*_root_source_argv(ctx), "service", "show", target_ref],
                "Show service",
            )
        ]

    _emit_result(ctx, result, ["doctor", target_type], actions)
    return 0 if result.status in {"healthy", "unknown"} else 1
