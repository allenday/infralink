"""Typed operator operations projected through Infralink's public adapters.

Every public transport consumes these Pydantic contracts; transport code must
not introduce an independent validation path.
"""

from __future__ import annotations

import re
import subprocess
from ipaddress import ip_address, ip_network
from pathlib import Path
from typing import Any, Literal, NoReturn, cast

import click
from agent_surface import App, OperationError, OperationOutcome
from agent_surface.adapters.click import ClickAdapter
from agent_surface.adapters.mcp import MCPAdapter
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from infralink.cli.contracts import (
    AppListResult,
    AppShowResult,
    DiagramProjectResult,
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
    OperationStatusResult,
    OperationSummary,
    TargetReconcileStatus,
)
from infralink.cli.queries import entity_not_found, list_services
from infralink.fleet.validation import FleetValidationResult, validate_fleet
from infralink.observation.api import ProjectValidationError, project_v2_topology_diagram
from infralink.observation.models import CanonicalId, HostId
from infralink.observation.topology import V2TopologyBoundsError
from infralink.observation.topology_diagrams import (
    V2TopologyRenderBoundsError,
    render_v2_dot,
    render_v2_mermaid,
)
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
from infralink.operator_sources import (
    OperatorInputs,
    SourceRequest,
    load_info_sources,
    load_registry,
    load_sources,
)


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
# The public MCP entrypoint starts with only the low-risk application reads.
# This registry is the sole authority for their CLI and MCP projections.
app_surface = App("infralink", shared_input_model=OperatorInputs)
# Diagram projection owns explicit observation sources and therefore cannot
# inherit the Registry/edge selector shared by topology operations.
diagram_surface = App("infralink-diagram")
_canonical_id_adapter = TypeAdapter(CanonicalId)
_host_id_adapter = TypeAdapter(HostId)


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
    from infralink.operator_actions import OperatorActionProvider

    return MCPAdapter(
        operator_surface,
        action_provider=OperatorActionProvider(),
        envelope_renderer=InfralinkEnvelopeRenderer(),
    )


def fleet_click_command() -> click.Group:
    """Return the generated fleet subtree mounted under the public root."""
    from infralink.agent_surface import mounted_click_command
    from infralink.operator_actions import OperatorActionProvider

    root = mounted_click_command(
        operator_surface,
        action_provider=OperatorActionProvider(),
    )
    command = root.get_command(click.Context(root), "fleet")
    assert isinstance(command, click.Group)
    return command


def info_click_command() -> click.Command:
    """Return the generated info leaf mounted under the public root."""
    from infralink.agent_surface import mounted_click_command
    from infralink.operator_actions import OperatorActionProvider

    root = mounted_click_command(
        operator_surface,
        action_provider=OperatorActionProvider(),
    )
    command = root.get_command(click.Context(root), "info")
    assert isinstance(command, click.Command)
    return command


def app_click_command() -> click.Group:
    """Return the generated application subtree mounted under the root CLI."""
    from infralink.agent_surface import (
        AppEnvelopeRenderer,
        app_render_options,
        mounted_click_command,
    )
    from infralink.app_actions import AppActionProvider

    root = mounted_click_command(
        app_surface,
        action_provider=AppActionProvider(),
        envelope_renderer=AppEnvelopeRenderer(),
        render_options=app_render_options(),
        operation_error_exit_code_override=_app_operation_error_exit_code,
    )
    command = root.get_command(click.Context(root), "app")
    assert isinstance(command, click.Group)
    return command


def _app_operation_error_exit_code(code: str) -> int:
    """Keep the migrated app family's missing-source usage exit stable."""
    if code == "configuration_required":
        return 2
    from infralink.agent_surface import operation_error_exit_code

    return operation_error_exit_code(code)


def app_mcp_adapter() -> MCPAdapter:
    """Project the one public application registry through native MCP."""
    from infralink.agent_surface import AppEnvelopeRenderer, app_render_options
    from infralink.app_actions import AppActionProvider

    return MCPAdapter(
        app_surface,
        action_provider=AppActionProvider(),
        envelope_renderer=AppEnvelopeRenderer(),
        render_options=app_render_options(),
    )


def diagram_mcp_adapter() -> MCPAdapter:
    """Build the diagram-local MCP adapter used by its adapter contract tests."""
    from infralink.agent_surface import InfralinkEnvelopeRenderer

    return MCPAdapter(diagram_surface, envelope_renderer=InfralinkEnvelopeRenderer())


class HostListRequest(SourceRequest):
    """List hosts from one explicit registry source."""


class ServiceListRequest(SourceRequest):
    """List logical services from one explicit registry source."""


class EdgeListRequest(SourceRequest):
    """List declared edges from one explicit registry source."""


class InfoRequest(SourceRequest):
    """Summarize one explicit registry source."""


class FleetValidateRequest(SourceRequest):
    """Validate one declared fleet without host-side operations."""

    host: str | None = Field(default=None, min_length=1)
    strict: bool = False
    live: bool = False


class DiagramProjectRequest(_OperationModel):
    """Explicit V2 declaration sources for one read-only inline diagram."""

    source: tuple[Path, ...] = Field(min_length=1)
    scope: Literal["full", "host", "service"] = "full"
    host: str | None = Field(default=None, min_length=1)
    service: str | None = Field(default=None, min_length=1)
    syntax: Literal["mermaid", "dot"] = "mermaid"

    @model_validator(mode="after")
    def require_exact_scope_selector(self) -> DiagramProjectRequest:
        if self.scope == "full" and self.host is None and self.service is None:
            return self
        if self.scope == "host" and _is_host_id(self.host) and self.service is None:
            return self
        if self.scope == "service" and self.host is None and self.service is not None:
            host_id, separator, service_id = self.service.partition("/")
            if (
                separator
                and "/" not in service_id
                and service_id
                and _is_host_id(host_id)
                and _is_canonical_id(service_id)
            ):
                return self
        raise ValueError("scope requires its exact selector combination")


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


class HostCreateRequest(SourceRequest):
    """Render or explicitly write one host scaffold in an operator checkout."""

    name: str = Field(min_length=1)
    address: str = Field(min_length=1)
    write: bool = False


class OperationStatusRequest(SourceRequest):
    """Read one declared host-local reconcile operation."""

    operation_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})


class HostCreateAddress(_OperationModel):
    field: Literal["tailscale_ip", "tailscale_name"]
    value: str
    reason: str


class HostCreateResult(_OperationModel):
    mode: Literal["dry_run", "written"]
    host_id: str
    address: HostCreateAddress
    manifest_path: Path | None
    manifest: dict[str, Any]
    write_state: Literal["local_uncommitted"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    git_worktree: Path | None = Field(default=None, exclude_if=lambda value: value is None)


class HostBootstrapOperationResult(_OperationModel):
    result: HostBootstrapPlanResult
    succeeded: bool


@operator_surface.operation(  # type: ignore[type-var]
    "host.bootstrap", summary="Plan or apply declared host bootstrap", idempotent=True
)
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


@operator_surface.operation(  # type: ignore[type-var]
    "host.logs", summary="Read bounded evidence from a host reconcile run", read_only=True
)
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


@operator_surface.operation(  # type: ignore[type-var]
    "host.status", summary="Read a host timer and latest reconcile status", read_only=True
)
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


@operator_surface.operation(  # type: ignore[type-var]
    "host.verifier", summary="Inspect public host verifier facts", read_only=True
)
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


@operator_surface.operation(  # type: ignore[type-var]
    "host.apply", summary="Plan or submit declared host reconciliation", idempotent=True
)
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


@operator_surface.operation(  # type: ignore[type-var]
    "host.create", summary="Render or write a declared host scaffold", idempotent=False
)
def host_create_operation(request: HostCreateRequest) -> HostCreateResult:
    """Use one typed authoring handler for dry-run and explicit writes."""
    from infralink.operator_operations.host_authoring import create_host

    try:
        return HostCreateResult.model_validate(create_host(request))
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation(  # type: ignore[type-var]
    "operation.status", summary="Read declared host reconcile progress", read_only=True
)
def operation_status_operation(request: OperationStatusRequest) -> OperationStatusResult:
    """Read one host-local reconcile operation through the declared SSH provider."""
    from infralink.cli.operations import operation_provider, resolve_apply_request

    if request.operation_id.startswith("op_"):
        raise OperationError(
            "provider_unavailable",
            "Legacy control-plane operation status is unavailable",
            details=({"operation_id": request.operation_id},),
            fix="Start a new declared host-local apply operation.",
        )
    sources = load_registry(request)
    parts = request.operation_id.split("/", 2)
    host_ref = parts[1] if len(parts) == 3 and parts[0] == "ssh" else request.operation_id
    target_host = sources.registry.get(host_ref)
    if target_host is None:
        _raise_operation_failure(entity_not_found("host", host_ref))
    try:
        record = operation_provider().status(
            request.operation_id,
            resolve_apply_request(sources.registry_path / "hosts", target_host),
        )
    except Exception as error:
        _raise_operation_failure(error)
    target = DoctorTarget.model_validate(record.target) if record.target is not None else None
    return OperationStatusResult(
        operation=OperationSummary(
            id=record.id,
            state=cast(Literal["queued", "applying", "converged", "failed"], record.state),
        ),
        target=target,
        failure=record.failure,
    )


@operator_surface.operation("host.list", summary="List declared hosts", read_only=True)  # type: ignore[type-var]
def host_list(request: HostListRequest) -> HostListResult:
    """Return the registry host list without a Click context."""
    return list_declared_hosts(request)


@operator_surface.operation("host.show", summary="Show one declared host", read_only=True)  # type: ignore[type-var]
def host_show(request: HostShowRequest) -> HostShowResult:
    """Return one bounded host view without a Click context."""
    return show_declared_host(request)


@operator_surface.operation("service.list", summary="List declared services", read_only=True)  # type: ignore[type-var]
def service_list(request: ServiceListRequest) -> ServiceListResult:
    """Return the registry service list without a Click context."""
    return list_declared_services(request)


@operator_surface.operation("service.show", summary="Show one declared service", read_only=True)  # type: ignore[type-var]
def service_show(request: ServiceShowRequest) -> ServiceShowResult:
    """Return one bounded service view without a Click context."""
    return show_declared_service(request)


@operator_surface.operation("edge.list", summary="List declared edges", read_only=True)  # type: ignore[type-var]
def edge_list(request: EdgeListRequest) -> EdgeListResult:
    """Return the selected registry edge list without a Click context."""
    return list_declared_edges(request)


@operator_surface.operation("edge.show", summary="Show one declared edge", read_only=True)  # type: ignore[type-var]
def edge_show(request: EdgeShowRequest) -> EdgeShowResult:
    """Return one bounded edge view without a Click context."""
    return show_declared_edge(request)


@app_surface.operation("app.list", summary="List declared applications", read_only=True)  # type: ignore[type-var]
def app_list(request: SourceRequest) -> AppListResult:
    """Return application IDs without a Click context."""
    return list_declared_apps(request)


@app_surface.operation("app.show", summary="Show one declared application", read_only=True)  # type: ignore[type-var]
def app_show(request: AppShowRequest) -> AppShowResult:
    """Return one bounded application view without a Click context."""
    return show_declared_app(request)


@operator_surface.operation("info", summary="Summarize declared topology", read_only=True)  # type: ignore[type-var]
def info(request: InfoRequest) -> InfoResult:
    """Return topology counts and the exact resolved declaration sources."""
    sources = load_info_sources(request)
    return InfoResult(
        sources=InfoSources(registry=str(sources.registry_path), edges=str(sources.edges_path)),
        summary=InfoSummary(
            host_count=len(sources.registry),
            service_count=len(list_services(sources.registry, sources.edges).items),
            edge_count=len(sources.edges),
        ),
    )


@operator_surface.operation(  # type: ignore[type-var]
    "fleet.validate", summary="Validate declared fleet topology", read_only=True
)
def fleet_validate(request: FleetValidateRequest) -> OperationOutcome[FleetValidationResult]:
    """Return deterministic declared-state diagnostics without repairing anything."""
    result = validate_fleet(
        load_sources(request), host=request.host, strict=request.strict, live=request.live
    )
    return OperationOutcome(result, exit_code=0 if result.valid else 1)


@diagram_surface.operation(  # type: ignore[type-var]
    "diagram.project", summary="Project a declared V2 topology diagram", read_only=True
)
def diagram_project(request: DiagramProjectRequest) -> DiagramProjectResult:
    """Render declared V2 topology inline without selecting or changing host state."""
    try:
        projection_result = project_v2_topology_diagram(
            request.source,
            focal_host_id=request.host,
            focal_service_instance_ref=request.service,
        )
    except V2TopologyBoundsError as error:
        raise OperationError(
            "diagram_topology_bounds_exceeded",
            "V2 topology declaration exceeds the projection item limit",
            details=(),
            fix="Reduce or split the full declaration; narrowing diagram focus does not reduce this bound.",
        ) from error
    except ProjectValidationError as error:
        first = error.report.diagnostics.diagnostics[0]
        raise OperationError(
            "diagram_source_invalid",
            "V2 topology source could not be projected",
            details=(
                {
                    "code": first.code,
                    "path": first.location.path,
                    "pointer": first.location.pointer,
                },
            ),
            fix="Supply valid infralink.observation/v2 source declarations.",
        ) from None
    projection = projection_result.projection
    try:
        source = (
            render_v2_mermaid(projection)
            if request.syntax == "mermaid"
            else render_v2_dot(projection)
        )
    except V2TopologyRenderBoundsError as error:
        raise OperationError(
            "diagram_render_bounds_exceeded",
            "V2 topology diagram exceeds the inline render limit",
            details=(),
            fix="Narrow the diagram scope to a host or service and retry.",
        ) from error
    focus = request.host if request.scope == "host" else request.service
    return DiagramProjectResult(
        syntax=request.syntax,
        scope=request.scope,
        resolved_focus=focus,
        node_count=len(projection.nodes),
        edge_count=len(projection.edges),
        source=source,
    )


def _is_host_id(value: str | None) -> bool:
    if value is None:
        return False
    try:
        _host_id_adapter.validate_python(value)
    except ValidationError:
        return False
    return True


def _is_canonical_id(value: str | None) -> bool:
    if value is None:
        return False
    try:
        _canonical_id_adapter.validate_python(value)
    except ValidationError:
        return False
    return True


@operator_surface.operation(  # type: ignore[type-var]
    "doctor.host.bootstrap_plan",
    summary="Plan declared host bootstrap prerequisites",
    read_only=True,
)
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
