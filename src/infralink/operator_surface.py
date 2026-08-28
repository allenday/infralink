"""Typed operator operations projected through Infralink's public adapters.

Every public transport consumes these Pydantic contracts; transport code must
not introduce an independent validation path.
"""

from __future__ import annotations

import re
import subprocess
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Literal, NoReturn, cast

from agent_surface import App, OperationError
from agent_surface.adapters.click import ClickAdapter
from agent_surface.adapters.mcp import MCPAdapter
from pydantic import BaseModel, ConfigDict, Field, model_validator

from infralink.cli.contracts import (
    AppListResult,
    AppShowResult,
    DoctorTarget,
    EdgeListResult,
    EdgeShowResult,
    HostBootstrapPlanResult,
    HostListResult,
    HostShowResult,
    InfoResult,
    InfoSources,
    InfoSummary,
    ServiceListResult,
    ServiceShowResult,
)
from infralink.cli.operation_contracts import (
    HostApplyPlan,
    HostApplyResult,
    HostDispatch,
    HostLogsResult,
    HostStatusResult,
    HostTimer,
    HostVerifierResult,
    LastReconcile,
    OperationSummary,
    TargetReconcileStatus,
)
from infralink.cli.queries import entity_not_found, list_services
from infralink.operator_operations.topology import (
    AppShowRequest,
    EdgeShowRequest,
    HostShowRequest,
    ServiceShowRequest,
    list_declared_apps,
    list_declared_edges,
    list_declared_hosts,
    list_declared_services,
    show_declared_app,
    show_declared_edge,
    show_declared_host,
    show_declared_service,
)
from infralink.operator_sources import OperatorInputs, SourceRequest, load_registry, load_sources


class _OperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DoctorBootstrapPlanRequest(SourceRequest):
    host_ref: str = Field(min_length=1)
    ssh_host: str = Field(min_length=1)
    declared_ssh_host: str = Field(min_length=1)


class DoctorBootstrapPlanResult(_OperationModel):
    argv: tuple[str, ...]
    ssh_host: str


operator_surface = App("infralink", shared_input_model=OperatorInputs)


def operator_click_adapter() -> ClickAdapter:
    """Build the one Click projection for typed operator operations."""
    from infralink.agent_surface import InfralinkEnvelopeRenderer, operation_error_exit_code

    return ClickAdapter(
        operator_surface,
        envelope_renderer=InfralinkEnvelopeRenderer(),
        operation_error_exit_code=operation_error_exit_code,
    )


def operator_mcp_adapter() -> MCPAdapter:
    """Build the one MCP projection for typed operator operations."""
    from infralink.agent_surface import InfralinkEnvelopeRenderer

    return MCPAdapter(operator_surface, envelope_renderer=InfralinkEnvelopeRenderer())


class HostListRequest(SourceRequest):
    """List hosts from one explicit registry source."""


class ServiceListRequest(SourceRequest):
    """List logical services from one explicit registry source."""


class EdgeListRequest(SourceRequest):
    """List declared edges from one explicit registry source."""


class InfoRequest(SourceRequest):
    """Summarize one explicit registry source."""


class HostBootstrapRequest(SourceRequest):
    """Transport-neutral input for one declared host bootstrap attempt."""

    host_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})
    ssh_host: str = Field(min_length=1)
    plan: bool = False
    apply: bool = False
    bws_token: str | None = Field(
        default=None,
        min_length=1,
        json_schema_extra={
            "sensitive": True,
            "cli": {"source": "stdin", "max_bytes": 8192},
        },
    )

    @model_validator(mode="after")
    def validate_mode(self) -> HostBootstrapRequest:
        if self.plan and self.apply:
            raise ValueError("pass at most one of plan or apply")
        if self.apply and self.bws_token is None:
            raise ValueError("apply requires bws_token")
        return self


class HostLogsRequest(SourceRequest):
    """Read bounded evidence for one declared host reconcile run."""

    host_ref: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})
    last_run: bool = Field(
        json_schema_extra={"cli": {"kind": "option"}},
        description="Require the latest reconcile run evidence.",
    )
    diagnostic: bool = Field(
        default=False,
        description="Read target-local adapter diagnostics instead of public evidence.",
    )

    @model_validator(mode="after")
    def require_last_run(self) -> HostLogsRequest:
        if not self.last_run:
            raise ValueError("last_run must be true")
        return self


class HostTargetRequest(SourceRequest):
    """Select one declared host for a typed control-plane query."""

    host_ref: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})


class HostApplyRequest(HostTargetRequest):
    """Submit one idempotent reconcile request or inspect its exact plan."""

    dry_run: bool = False
    wait: bool = False
    timeout: int = Field(default=300, ge=1, le=3600)


class HostBootstrapOperationResult(_OperationModel):
    result: HostBootstrapPlanResult
    succeeded: bool


@operator_surface.operation(
    "host.bootstrap", summary="Plan or apply declared host bootstrap", idempotent=True
)  # type: ignore[untyped-decorator]
def host_bootstrap_operation(request: HostBootstrapRequest) -> HostBootstrapOperationResult:
    """Execute bootstrap from an explicit source, without a Click parse context."""
    from infralink.cli.main import Context
    from infralink.operator_operations.host_bootstrap import execute_bootstrap

    sources = load_registry(request)
    context = Context()
    context.registry_path = sources.registry_path
    context.edges_path = request.edges
    context._registry = sources.registry
    result, _actions, succeeded = execute_bootstrap(context, request)
    return HostBootstrapOperationResult(result=result, succeeded=succeeded)


@operator_surface.operation(
    "host.logs", summary="Read bounded evidence from a host reconcile run", read_only=True
)  # type: ignore[untyped-decorator]
def host_logs_operation(request: HostLogsRequest) -> HostLogsResult:
    """Execute the one typed target-log query shared by all public transports."""
    from infralink.cli.operations import (
        inspect_target_diagnostic,
        inspect_target_logs,
        resolve_apply_request,
    )

    sources = load_registry(request)
    target = sources.registry.get(request.host_ref)
    if target is None:
        _raise_operation_failure(entity_not_found("host", request.host_ref))
    try:
        apply_request = resolve_apply_request(sources.registry_path / "hosts", target)
        lines = (
            inspect_target_diagnostic(apply_request)
            if request.diagnostic
            else inspect_target_logs(apply_request)
        )
    except Exception as error:
        _raise_operation_failure(error)
    return HostLogsResult(
        target=DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name),
        lines=lines,
    )


@operator_surface.operation(
    "host.status", summary="Read a host timer and latest reconcile status", read_only=True
)  # type: ignore[untyped-decorator]
def host_status_operation(request: HostTargetRequest) -> HostStatusResult:
    """Read the declared target's status through the sole SSH provider."""
    from infralink.cli.operations import inspect_target_status, resolve_apply_request

    sources = load_registry(request)
    target = sources.registry.get(request.host_ref)
    if target is None:
        _raise_operation_failure(entity_not_found("host", request.host_ref))
    try:
        values = inspect_target_status(
            resolve_apply_request(sources.registry_path / "hosts", target)
        )
    except Exception as error:
        _raise_operation_failure(error)
    return HostStatusResult(
        target=DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name),
        **_target_reconcile_status(values).model_dump(),
    )


@operator_surface.operation(
    "host.verifier", summary="Inspect public host verifier facts", read_only=True
)  # type: ignore[untyped-decorator]
def host_verifier_operation(request: HostTargetRequest) -> HostVerifierResult:
    """Read the declared target's V2 signature verifier facts."""
    from infralink.cli.operations import inspect_verifier, resolve_apply_request

    sources = load_registry(request)
    target = sources.registry.get(request.host_ref)
    if target is None:
        _raise_operation_failure(entity_not_found("host", request.host_ref))
    try:
        verifier = inspect_verifier(resolve_apply_request(sources.registry_path / "hosts", target))
    except Exception as error:
        _raise_operation_failure(error)
    return HostVerifierResult(
        target=DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name),
        verifier=verifier,
    )


@operator_surface.operation(
    "host.apply", summary="Plan or submit declared host reconciliation", idempotent=True
)  # type: ignore[untyped-decorator]
def host_apply_operation(request: HostApplyRequest) -> HostApplyResult:
    """Use the only declared SSH provider for a host-local reconcile request."""
    from infralink.cli.errors import CliFailure
    from infralink.cli.operations import (
        inspect_target_status,
        operation_provider,
        resolve_apply_request,
        validate_target_ssh_identity,
        wait_for_terminal,
    )

    sources = load_registry(request)
    target = sources.registry.get(request.host_ref)
    if target is None:
        _raise_operation_failure(entity_not_found("host", request.host_ref))
    try:
        apply_request = resolve_apply_request(sources.registry_path / "hosts", target)
        doctor_target = DoctorTarget(
            type="host", id=target.uuid, canonical_name=target.canonical_name
        )
        if request.dry_run:
            revision = _registry_revision(sources.registry_path)
            validate_target_ssh_identity(apply_request)
            return HostApplyResult(
                target=doctor_target,
                dry_run=True,
                plan=HostApplyPlan(
                    registry_revision=revision,
                    dispatch_provider="ssh",
                    reconcile_mode="timer",
                    action_categories=["registry_checkout", "render", "reconcile"],
                ),
                ssh_host_identity="passed",
            )
        provider = operation_provider()
        try:
            record = provider.submit(apply_request)
        except CliFailure as failure:
            dispatch_status = failure.details.get("dispatch")
            if failure.code.value != "provider_unavailable" or dispatch_status not in {
                "rejected",
                "unavailable",
            }:
                raise
            return HostApplyResult(
                target=doctor_target,
                dispatch=HostDispatch(provider="ssh", status=dispatch_status),
                target_status=_target_reconcile_status(inspect_target_status(apply_request)),
            )
        if request.wait:
            record = wait_for_terminal(
                provider, record.id, apply_request, timeout_seconds=request.timeout
            )
        return HostApplyResult(
            operation=OperationSummary(
                id=record.id,
                state=cast(Literal["queued", "applying", "converged", "failed"], record.state),
            ),
            target=doctor_target,
            dispatch=HostDispatch(provider="ssh", status="accepted"),
            ssh_host_identity="passed",
            failure=record.failure,
        )
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation("host.list", summary="List declared hosts", read_only=True)  # type: ignore[untyped-decorator]
def host_list(request: HostListRequest) -> HostListResult:
    """Return the registry host list without a Click context."""
    return list_declared_hosts(request)


@operator_surface.operation("host.show", summary="Show one declared host", read_only=True)  # type: ignore[untyped-decorator]
def host_show(request: HostShowRequest) -> HostShowResult:
    """Return one bounded host view without a Click context."""
    return show_declared_host(request)


@operator_surface.operation("service.list", summary="List declared services", read_only=True)  # type: ignore[untyped-decorator]
def service_list(request: ServiceListRequest) -> ServiceListResult:
    """Return the registry service list without a Click context."""
    return list_declared_services(request)


@operator_surface.operation("service.show", summary="Show one declared service", read_only=True)  # type: ignore[untyped-decorator]
def service_show(request: ServiceShowRequest) -> ServiceShowResult:
    """Return one bounded service view without a Click context."""
    return show_declared_service(request)


@operator_surface.operation("edge.list", summary="List declared edges", read_only=True)  # type: ignore[untyped-decorator]
def edge_list(request: EdgeListRequest) -> EdgeListResult:
    """Return the selected registry edge list without a Click context."""
    return list_declared_edges(request)


@operator_surface.operation("edge.show", summary="Show one declared edge", read_only=True)  # type: ignore[untyped-decorator]
def edge_show(request: EdgeShowRequest) -> EdgeShowResult:
    """Return one bounded edge view without a Click context."""
    return show_declared_edge(request)


@operator_surface.operation("app.list", summary="List declared applications", read_only=True)  # type: ignore[untyped-decorator]
def app_list(request: SourceRequest) -> AppListResult:
    """Return application IDs without a Click context."""
    return list_declared_apps(request)


@operator_surface.operation("app.show", summary="Show one declared application", read_only=True)  # type: ignore[untyped-decorator]
def app_show(request: AppShowRequest) -> AppShowResult:
    """Return one bounded application view without a Click context."""
    return show_declared_app(request)


@operator_surface.operation("info", summary="Summarize declared topology", read_only=True)  # type: ignore[untyped-decorator]
def info(request: InfoRequest) -> InfoResult:
    """Return topology counts and the exact resolved declaration sources."""
    sources = load_sources(request)
    return InfoResult(
        sources=InfoSources(registry=str(sources.registry_path), edges=str(sources.edges_path)),
        summary=InfoSummary(
            host_count=len(sources.registry),
            service_count=len(list_services(sources.registry, sources.edges).items),
            edge_count=len(sources.edges),
        ),
    )


@operator_surface.operation(
    "doctor.host.bootstrap_plan",
    summary="Plan declared host bootstrap prerequisites",
    read_only=True,
)  # type: ignore[untyped-decorator]
def doctor_host_bootstrap_plan(request: DoctorBootstrapPlanRequest) -> DoctorBootstrapPlanResult:
    """Validate and build the only executable bootstrap-plan transition."""
    if not _is_tailnet_ipv4(request.declared_ssh_host):
        raise OperationError(
            "tailnet_address_required",
            "Host bootstrap requires a declared Tailnet IPv4 address",
            details=({"host": request.host_ref},),
            fix="Declare a 100.64.0.0/10 host address before planning bootstrap.",
        )
    if request.ssh_host != request.declared_ssh_host:
        raise OperationError(
            "bootstrap_transport_mismatch",
            "Bootstrap SSH host must exactly match the declared Tailnet IPv4",
            details=(
                {"host": request.host_ref, "declared_tailscale_ip": request.declared_ssh_host},
            ),
            fix="Pass the registry tailscale_ip with --ssh-host.",
        )
    return DoctorBootstrapPlanResult(
        argv=(
            "host",
            "bootstrap",
            request.host_ref,
            "--ssh-host",
            request.ssh_host,
            "--plan",
        ),
        ssh_host=request.ssh_host,
    )


def _is_tailnet_ipv4(address: str) -> bool:
    try:
        return ip_address(address) in ip_network("100.64.0.0/10")
    except ValueError:
        return False


def _target_reconcile_status(values: dict[str, str]) -> TargetReconcileStatus:
    result_value = values.get("unit_result")
    status = "success" if result_value == "success" else "failed" if result_value else "unknown"
    sha = values.get("registry_sha")
    active = values.get("unit_active") in {"active", "activating", "reloading"}
    return TargetReconcileStatus(
        reconcile_mode="timer",
        timer=HostTimer(
            active=values.get("timer_active") == "active",
            next_scheduled_at=values.get("timer_next") or None,
        ),
        in_progress=active,
        last_reconcile=LastReconcile(
            status=cast(Literal["success", "failed", "unknown"], status),
            registry_sha=sha if re.fullmatch(r"[0-9a-f]{40}", sha or "") else None,
            finished_at=(
                values["finished_at"]
                if re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                    values.get("finished_at", ""),
                )
                else None
            ),
        ),
    )


def _registry_revision(registry_path: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(registry_path), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=False,
    )
    revision = completed.stdout.strip()
    if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise OperationError(
            "input_load_failed",
            "Selected registry revision could not be resolved",
            details=({"registry": str(registry_path)},),
            fix="Use a Git checkout containing the selected registry revision.",
        )
    return revision


def _raise_operation_failure(error: Exception) -> NoReturn:
    """Keep expected CLI domain failures as typed operation errors."""
    from infralink.cli.errors import CliFailure

    if isinstance(error, CliFailure):
        raise OperationError(
            error.code.value,
            error.message,
            details=(error.details,),
            fix=error.fix,
        ) from None
    raise error
