"""Typed operator operations projected through Infralink's public adapters.

Every public transport consumes these Pydantic contracts; transport code must
not introduce an independent validation path.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, NoReturn, cast, get_args, get_origin

from agent_surface import App, OperationError, OperationOutcome
from agent_surface.adapters.click import ClickAdapter
from agent_surface.adapters.mcp import MCPAdapter
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    TypeAdapter,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticCustomError

from infralink import __version__
from infralink.cli.contracts import (
    Action as DoctorAction,
)
from infralink.cli.contracts import (
    AnalyzeResult,
    AppListResult,
    AppShowResult,
    ArgumentDescriptor,
    ArtifactResult,
    CheckCommandResult,
    DiagramProjectResult,
    DoctorResult,
    DoctorTarget,
    EdgeListResult,
    EdgeShowResult,
    HelpNavigationAction,
    HelpResult,
    HelpSubcommand,
    HostBootstrapPlanResult,
    HostListResult,
    HostShowResult,
    InfoResult,
    InfoSources,
    InfoSummary,
    OptionDescriptor,
    PublisherRequestResult,
    RegistryHostGetResult,
    RegistryHostIdentity,
    RegistryHostPatchResult,
    RegistryMutation,
    ReleaseAttestationResult,
    ReleaseCandidateResult,
    ReleaseInspectResult,
    ResolveResult,
    SecretsInspectResult,
    ServiceListResult,
    ServiceShowResult,
    VersionResult,
)
from infralink.cli.observation_contracts import (
    CapabilitiesResult,
    ExplainResult,
    ObservationPlan,
    ObservationReadinessSuite,
    ProjectObservationResult,
    ProjectReadinessResult,
    ProjectSecretsResult,
    ProjectViewResult,
    SourceProvenanceResult,
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
from infralink.operator_config import OperatorConfigError, configured_registry
from infralink.operator_operations.analyze import AnalyzeRequest, analyze_declared_registry
from infralink.operator_operations.docs import DocsRequest, generate_declared_docs
from infralink.operator_operations.doctor import (
    doctor_host_bootstrap_plan as _doctor_host_bootstrap_plan,
)
from infralink.operator_operations.edge_health import (
    EdgeCheckRequest,
    EdgeResolveRequest,
    check_declared_edges,
    resolve_declared_edge,
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
    SourceRequest,
    load_info_sources,
    load_registry,
    load_sources,
)


class _OperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class VersionRequest(_OperationModel):
    """Version inspection has no source or provider inputs."""


# This is the sole public operation registry.  Every source-consuming request
# owns its own selector fields; source-independent operations simply have no
# selector.  That keeps the generated Click and MCP projections bijective
# without making an ambient root selector a second source authority.
operator_surface = App("infralink")
_canonical_id_adapter = TypeAdapter(CanonicalId)
_host_id_adapter = TypeAdapter(HostId)


def operator_click_adapter() -> ClickAdapter:
    """Build the one Click projection for typed operator operations."""
    from infralink.agent_surface import (
        PublicEnvelopeRenderer,
        operation_error_exit_code,
        operator_render_options,
    )
    from infralink.operator_actions import InfralinkActionProvider

    return ClickAdapter(
        operator_surface,
        action_provider=InfralinkActionProvider(),
        envelope_renderer=PublicEnvelopeRenderer(),
        render_options=operator_render_options(),
        operation_error_exit_code=operation_error_exit_code,
    )


def operator_mcp_adapter() -> MCPAdapter:
    """Build the one MCP projection for typed operator operations."""
    from infralink.agent_surface import PublicEnvelopeRenderer, operator_render_options
    from infralink.operator_actions import InfralinkActionProvider

    return MCPAdapter(
        operator_surface,
        action_provider=InfralinkActionProvider(),
        envelope_renderer=PublicEnvelopeRenderer(),
        render_options=operator_render_options(),
    )


class HostListRequest(SourceRequest):
    """List hosts from one explicit registry source."""


class ServiceListRequest(SourceRequest):
    """List logical services from one explicit registry source."""


class EdgeListRequest(SourceRequest):
    """List declared edges from one explicit registry source."""


class InfoRequest(SourceRequest):
    """Summarize one explicit registry source."""


class DoctorRequest(SourceRequest):
    """Inspect declared and live observation evidence for an optional target."""

    observation_plan: Path | None = None
    adapter_bindings: Path | None = None
    declaration_only: bool = Field(
        default=False, json_schema_extra={"cli": {"options": ["--validate"]}}
    )
    gatus_url: str | None = Field(default=None, min_length=1)
    # This is an environment variable name, never the token value itself.
    gatus_token_env: str = Field(default="INFRALINK_GATUS_TOKEN", min_length=1)
    target_type: Literal["host", "service", "edge", "profile"] | None = Field(
        default=None,
        json_schema_extra={"cli": {"kind": "argument"}},
    )
    target_ref: str | None = Field(
        default=None,
        min_length=1,
        json_schema_extra={"cli": {"kind": "argument"}},
    )

    @model_validator(mode="after")
    def require_complete_target(self) -> DoctorRequest:
        if (self.target_type is None) == (self.target_ref is None):
            return self
        raise ValueError("target_type and target_ref must be supplied together")


class DoctorOperationResult(DoctorResult):
    """Doctor result plus private typed action state for adapter projection."""

    _actions: tuple[DoctorAction, ...] = PrivateAttr(default=())


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


class RegistryHostGetRequest(SourceRequest):
    """Read one host declaration from an operator Registry checkout."""

    host_ref: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})


class RegistryHostPatchRequest(RegistryHostGetRequest):
    """Preview or explicitly write existing host fields in an operator checkout."""

    assignments: tuple[str, ...] = Field(
        min_length=1,
        description="Repeat --set with PATH=YAML_VALUE, PATH=@text:FILE, or PATH=@yaml:FILE.",
        json_schema_extra={"cli": {"options": ["--set"], "multiple": True}},
    )
    write: bool = False


class OperationStatusRequest(SourceRequest):
    """Read one declared host-local reconcile operation."""

    operation_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})


class CapabilitiesRequest(_OperationModel):
    """Read the fixed offline observation capability contract."""


class ExplainRequest(_OperationModel):
    """Explain one stable observation diagnostic code."""

    error_code: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})


class HelpRequest(_OperationModel):
    """Select one dotted public operation path for generated help."""

    path: str | None = Field(
        default=None,
        description="Dotted operation path; omit to list the public root.",
    )

    @field_validator("path")
    @classmethod
    def normalize_path(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().replace(" ", ".")
        if not normalized or any(not segment for segment in normalized.split(".")):
            raise ValueError("path must be a dotted public operation path")
        return normalized


class ObservationProjectRequest(_OperationModel):
    """Shared immutable inputs for one offline observation projection."""

    source: Path
    # Agent Surface projects scalar strings consistently through Click and MCP.
    # Convert at the domain boundary instead of creating a transport-only
    # datetime parser.
    as_of: str = Field(min_length=1)
    registry_revision: str | None = None

    @field_validator("as_of", mode="before")
    @classmethod
    def normalize_as_of(cls, value: str | datetime) -> str:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError as error:
                raise PydanticCustomError(
                    "rfc3339_timestamp", "as_of must be an RFC 3339 timestamp"
                ) from error
        else:
            raise PydanticCustomError("rfc3339_timestamp", "as_of must be an RFC 3339 timestamp")
        if parsed.tzinfo is None:
            raise PydanticCustomError("rfc3339_offset", "as_of must include a UTC offset")
        return parsed.isoformat()


class ObservationProjectItemRequest(ObservationProjectRequest):
    """Select one named view or readiness suite from a projected plan."""

    item_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})


class ReleaseInspectRequest(_OperationModel):
    """Select immutable local release validation and admission documents."""

    release_validation: Path
    admission: Path


class ReleaseCandidateRequest(_OperationModel):
    """Select one immutable release candidate document."""

    candidate: Path


class ReleaseAttestationRequest(_OperationModel):
    """Select one immutable publisher attestation document."""

    attestation: Path


class ReleasePublisherRequest(_OperationModel):
    """Select one rendered request or the bounded legacy rendering inputs."""

    candidate: Path | None = None
    admission: Path | None = None
    publisher_request: Path | None = None


class SecretsInspectRequest(SourceRequest):
    """Select bounded declared secret-reference metadata from a Registry checkout."""

    requested_ref: str | None = Field(
        default=None, json_schema_extra={"cli": {"options": ["--ref"]}}
    )
    limit: int = Field(default=20, ge=1, le=1000)
    cursor: str | None = None
    collection: str | None = None


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


class RegistryHostGetOperationResult(RegistryHostGetResult):
    """Public host declaration plus the private selected checkout for actions."""

    _checkout: Path = PrivateAttr()


class RegistryHostPatchOperationResult(RegistryHostPatchResult):
    """Public mutation preview plus the private selected checkout for actions."""

    _checkout: Path = PrivateAttr()


class HostBootstrapOperationResult(_OperationModel):
    result: HostBootstrapPlanResult
    succeeded: bool


@operator_surface.operation(  # type: ignore[type-var]
    "host.bootstrap", summary="Plan or apply declared host bootstrap", idempotent=True
)
def host_bootstrap_operation(
    request: HostBootstrapRequest,
) -> OperationOutcome[HostBootstrapOperationResult]:
    """Execute bootstrap from an explicit source, without a Click parse context."""
    from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
    from infralink.cli.main import Context
    from infralink.operator_operations.host_bootstrap import execute_bootstrap

    if request.apply and request.bws_token is None:
        _raise_operation_failure(
            CliFailure(
                code=ErrorCode.CONFIGURATION_REQUIRED,
                message="Host bootstrap apply requires a BWS token on standard input",
                exit_code=ExitCode.INPUT_ERROR,
                fix="Pipe the host machine token to infralink host bootstrap --bws-token-stdin --apply",
                details={"host": request.host_id, "requirement": "bws_token_stdin"},
            )
        )

    sources = load_registry(request)
    context = Context()
    context.registry_path = sources.registry_path
    context.edges_path = request.edges
    context._registry = sources.registry
    try:
        result, _actions, succeeded = execute_bootstrap(context, request)
    except Exception as error:
        _raise_operation_failure(error)
    return OperationOutcome(
        HostBootstrapOperationResult(result=result, succeeded=succeeded),
        exit_code=0 if succeeded else 1,
    )


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
def host_verifier_operation(
    request: HostTargetRequest,
) -> OperationOutcome[HostVerifierResult]:
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
    result = HostVerifierResult(
        target=DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name),
        verifier=verifier,
    )
    valid = verifier.signature_verification == "passed" and not verifier.unavailable
    return OperationOutcome(result, exit_code=0 if valid else 1)


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


def _registry_authoring_context(request: SourceRequest) -> tuple[Any, Path]:
    """Create the retained authoring evaluator context from one checkout-root input."""
    from infralink.cli.main import Context

    try:
        selected = request.registry or configured_registry()
    except OperatorConfigError as error:
        raise OperationError(
            "input_load_failed",
            "Operator configuration could not be loaded",
            details=({"source": "operator_config", "path": str(error)},),
            fix="Correct INFRALINK_CONFIG or pass an explicit registry checkout root.",
        ) from None
    if selected is None:
        raise OperationError(
            "configuration_required",
            "Registry source is required",
            details=({"source": "registry"},),
            fix="Pass a registry checkout root or configure INFRALINK_CONFIG.",
        )
    checkout = selected.expanduser().resolve()
    hosts = checkout / "hosts"
    resolved_hosts = hosts.resolve()
    if not checkout.is_dir() or not hosts.is_dir() or checkout not in resolved_hosts.parents:
        raise OperationError(
            "source_invalid",
            "Registry authoring requires a checkout root with a contained hosts directory",
            details=({"source": "registry", "path": str(checkout)},),
            fix="Pass an ordinary Registry checkout root, not a symlinked hosts directory.",
        )
    context = Context()
    context.registry_path = checkout
    context.hosts_path = resolved_hosts
    return context, checkout


@operator_surface.operation(  # type: ignore[type-var]
    "registry.host.get", summary="Show an authoritative host declaration", read_only=True
)
def registry_host_get_operation(request: RegistryHostGetRequest) -> RegistryHostGetOperationResult:
    """Use the retained authoring evaluator through one typed source contract."""
    from infralink.cli import registry_authoring

    try:
        context, checkout = _registry_authoring_context(request)
        root = registry_authoring._registry_root(context)
        host_id, manifest_path, _source, _document, declaration = registry_authoring._find_host(
            root, request.host_ref
        )
        result = RegistryHostGetOperationResult(
            host=RegistryHostIdentity(id=host_id, canonical_name=declaration.get("canonical_name")),
            manifest_path=str(manifest_path),
            declaration=registry_authoring._public_value(declaration),
        )
        result._checkout = checkout
        return result
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation(  # type: ignore[type-var]
    "registry.host.patch",
    summary="Preview or write a typed host declaration mutation",
    idempotent=True,
)
def registry_host_patch_operation(
    request: RegistryHostPatchRequest,
) -> RegistryHostPatchOperationResult:
    """Apply the retained mutation evaluator behind explicit typed write intent."""
    from copy import deepcopy

    from infralink.cli import registry_authoring

    try:
        context, checkout = _registry_authoring_context(request)
        root = registry_authoring._registry_root(context, for_write=request.write)
        host_id, manifest_path, source, document, _declaration = registry_authoring._find_host(
            root, request.host_ref
        )
        assignments = registry_authoring._resolve_assignments(request.assignments)
        candidate = deepcopy(document)
        candidate_hosts = candidate.get("hosts")
        assert isinstance(candidate_hosts, dict)
        candidate_declaration = candidate_hosts[host_id]
        assert isinstance(candidate_declaration, dict)
        changes = [
            registry_authoring._apply_assignment(candidate_declaration, assignment)
            for assignment in assignments
        ]
        registry_authoring._validate_candidate(candidate, host_id)
        if request.write:
            registry_authoring._write_document(
                manifest_path,
                registry_authoring._replace_scalar_assignments(source, host_id, assignments),
            )
        result = RegistryHostPatchOperationResult(
            mode="written" if request.write else "preview",
            host=RegistryHostIdentity(
                id=host_id, canonical_name=candidate_declaration.get("canonical_name")
            ),
            manifest_path=str(manifest_path),
            changes=[RegistryMutation(**change) for change in changes],
        )
        result._checkout = checkout
        return result
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation(  # type: ignore[type-var]
    "secrets.inspect", summary="Inspect declared secret-reference metadata", read_only=True
)
def secrets_inspect_operation(request: SecretsInspectRequest) -> SecretsInspectResult:
    """Reuse the retained offline collector without loading a secret provider."""
    from infralink.cli import secrets
    from infralink.cli.main import Context

    sources = load_sources(request)
    context = Context()
    context.registry_path = sources.registry_path
    context.edges_path = sources.edges_path
    context._registry = sources.registry
    context._edges = sources.edges
    try:
        result, _statuses = secrets._inspect_result(
            context,
            requested_ref=request.requested_ref,
            limit=request.limit,
            cursor=request.cursor,
            collection=request.collection,
        )
        return result
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation("version", summary="Show CLI and schema versions", read_only=True)  # type: ignore[type-var]
def version_operation(request: VersionRequest) -> VersionResult:
    """Return static package metadata through the canonical operation registry."""
    return VersionResult(version=__version__, cli_schema_version="infralink.cli/v1")


@operator_surface.operation(  # type: ignore[type-var]
    "capabilities", summary="Describe the offline observation contract surface", read_only=True
)
def capabilities_operation(_request: CapabilitiesRequest) -> CapabilitiesResult:
    """Return the established observation capability declaration."""
    from infralink.observation.models import HealthEvaluator, LogEvaluator, MetricsEvaluator

    return CapabilitiesResult(
        document_schema_versions=["infralink.observation/v1", "infralink.observation/v2"],
        plan_schema_versions=["infralink.plan.v1"],
        input_schemas={
            **{
                name: f"infralink/schemas/observation/v1/{name}.json"
                for name in (
                    "profile",
                    "instance",
                    "application",
                    "dependency",
                    "secrets",
                    "operations-view",
                    "readiness-suite",
                )
            },
            "v2-document": "infralink/schemas/observation/v2/document.json",
        },
        evaluator_types={
            "health": sorted(value.value for value in HealthEvaluator),
            "metrics": sorted(value.value for value in MetricsEvaluator),
            "logs": sorted(value.value for value in LogEvaluator),
        },
        projections=["observation", "secrets", "view", "readiness"],
    )


@operator_surface.operation(  # type: ignore[type-var]
    "explain", summary="Explain one offline observation diagnostic", read_only=True
)
def explain_operation(request: ExplainRequest) -> ExplainResult:
    """Resolve an existing diagnostic without introducing a second taxonomy."""
    from infralink.cli.observation import _explain_result
    from infralink.observation import DiagnosticCodeNotFoundError

    try:
        return _explain_result(request.error_code)
    except DiagnosticCodeNotFoundError as error:
        raise OperationError(
            "diagnostic-code-not-found",
            str(error),
            details=({"available_codes": list(error.available_codes)},),
            fix="Use one of the available diagnostic codes.",
        ) from None


@operator_surface.operation(  # type: ignore[type-var]
    "help", summary="Discover public Infralink operations", read_only=True
)
def help_operation(request: HelpRequest) -> HelpResult:
    """Render public help directly from the one registered operation tree."""
    selected = tuple(request.path.split(".")) if request.path is not None else ()
    definitions = {item.name: item for item in operator_surface.operations.list()}
    available_paths = {tuple(name.split(".")) for name in definitions}
    if selected and not any(path[: len(selected)] == selected for path in available_paths):
        raise OperationError(
            "entity_not_found",
            "Public operation path was not found",
            details=({"entity_type": "operation", "requested_id": request.path},),
            fix="Run infralink help to discover registered operations.",
        )
    definition = definitions.get(".".join(selected))
    children = sorted(
        {
            path[len(selected)]
            for path in available_paths
            if len(path) > len(selected) and path[: len(selected)] == selected
        }
    )
    return HelpResult(
        path=list(selected),
        description=(
            definition.summary if definition is not None else "Infralink public operation registry"
        ),
        arguments=[] if definition is None else _operation_arguments(definition.input_model),
        options=[] if definition is None else _operation_options(definition.input_model),
        examples=["infralink help", "infralink help --path host.show"],
        children=[
            HelpSubcommand(
                name=child,
                summary=_help_child_summary(definitions, selected, child),
                action=HelpNavigationAction(
                    command=f"infralink help --path {'.'.join((*selected, child))}",
                    argv=["infralink", "help", "--path", ".".join((*selected, child))],
                ),
            )
            for child in children
        ],
    )


def _help_child_summary(definitions: dict[str, Any], selected: tuple[str, ...], child: str) -> str:
    """Read a child summary without loading a second transport tree."""
    path = (*selected, child)
    prefix = ".".join(path)
    exact = definitions.get(prefix)
    if exact is not None:
        return str(exact.summary)
    first = next(
        item for name, item in sorted(definitions.items()) if name.startswith(f"{prefix}.")
    )
    return str(first.summary)


def _operation_arguments(model: type[BaseModel]) -> list[ArgumentDescriptor]:
    return [
        ArgumentDescriptor(
            name=name,
            type=_operation_field_type(field.annotation),
            required=field.is_required(),
        )
        for name, field in model.model_fields.items()
        if _operation_field_kind(field) == "argument"
    ]


def _operation_options(model: type[BaseModel]) -> list[OptionDescriptor]:
    return [
        OptionDescriptor(
            name=name,
            type=_operation_field_type(field.annotation),
            required=field.is_required(),
            description=field.description,
        )
        for name, field in model.model_fields.items()
        if _operation_field_kind(field) != "argument"
    ]


def _operation_field_kind(field: Any) -> str:
    raw_extra = field.json_schema_extra
    extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
    raw_cli = extra.get("cli")
    cli: dict[str, Any] = raw_cli if isinstance(raw_cli, dict) else {}
    return str(cli.get("kind", "option"))


def _operation_field_type(annotation: Any) -> str:
    origin = get_origin(annotation)
    if origin is Literal:
        return "choice"
    if origin in {list, tuple, set}:
        return "array"
    arguments = get_args(annotation)
    if type(None) in arguments:
        return _operation_field_type(next(item for item in arguments if item is not type(None)))
    if annotation is Path:
        return "path"
    if annotation is bool:
        return "boolean"
    if annotation is int:
        return "integer"
    return "string"


@operator_surface.operation(  # type: ignore[type-var]
    "project.observation", summary="Project one offline observation plan", read_only=True
)
def project_observation_operation(request: ObservationProjectRequest) -> ProjectObservationResult:
    """Project the existing planner result without a parallel source model."""
    from infralink.observation import ProjectValidationError, project

    try:
        projected = project(
            [request.source],
            as_of=_observation_as_of(request),
            registry_revision=request.registry_revision,
        )
    except ProjectValidationError as error:
        first = error.report.diagnostics.diagnostics[0]
        raise OperationError(
            first.code,
            first.message,
            details=(asdict(error.report.diagnostics),),
            fix="Validate the observation source and correct the reported diagnostic.",
        ) from None
    return ProjectObservationResult(
        plan=ObservationPlan.model_validate(projected.plan.model_dump(mode="python")),
        sources=tuple(SourceProvenanceResult(**asdict(source)) for source in projected.sources),
    )


def _project_observation_plan(request: ObservationProjectRequest) -> Any:
    from infralink.observation import ProjectValidationError, project

    try:
        return project(
            [request.source],
            as_of=_observation_as_of(request),
            registry_revision=request.registry_revision,
        ).plan
    except ProjectValidationError as error:
        first = error.report.diagnostics.diagnostics[0]
        raise OperationError(
            first.code,
            first.message,
            details=(asdict(error.report.diagnostics),),
            fix="Validate the observation source and correct the reported diagnostic.",
        ) from None


def _observation_as_of(request: ObservationProjectRequest) -> datetime:
    """Parse the one public RFC 3339 timestamp at the projection boundary."""
    return datetime.fromisoformat(request.as_of.replace("Z", "+00:00"))


@operator_surface.operation(  # type: ignore[type-var]
    "project.secrets", summary="Project offline secret requirements", read_only=True
)
def project_secrets_operation(request: ObservationProjectRequest) -> ProjectSecretsResult:
    """Return existing planner secret requirements without provider access."""
    plan = _project_observation_plan(request)
    return ProjectSecretsResult(
        plan_digest=plan.plan_digest or "",
        secret_requirements=plan.secret_requirements,
        secret_bindings=plan.secret_bindings,
        provider_aliases=plan.provider_aliases,
        opaque_identities=plan.opaque_identities,
    )


@operator_surface.operation(  # type: ignore[type-var]
    "project.view", summary="Project one offline operations view", read_only=True
)
def project_view_operation(request: ObservationProjectItemRequest) -> ProjectViewResult:
    """Select one declared operations view from the existing planner output."""
    plan = _project_observation_plan(request)
    selected = next((item for item in plan.operations_views if item.id == request.item_id), None)
    if selected is None:
        raise OperationError(
            "view-not-found",
            "View not found",
            details=({"view_id": request.item_id},),
            fix="Use a declared operations view identifier.",
        )
    return ProjectViewResult(plan_digest=plan.plan_digest or "", view=selected)


@operator_surface.operation(  # type: ignore[type-var]
    "project.readiness", summary="Project one offline readiness suite", read_only=True
)
def project_readiness_operation(request: ObservationProjectItemRequest) -> ProjectReadinessResult:
    """Select one declared readiness suite from the existing planner output."""
    plan = _project_observation_plan(request)
    selected = next((item for item in plan.readiness_suites if item.id == request.item_id), None)
    if selected is None:
        raise OperationError(
            "readiness-not-found",
            "Readiness not found",
            details=({"readiness_id": request.item_id},),
            fix="Use a declared readiness suite identifier.",
        )
    return ProjectReadinessResult(
        plan_digest=plan.plan_digest or "",
        readiness_suite=ObservationReadinessSuite.model_validate(
            selected.model_dump(mode="python")
        ),
    )


@operator_surface.operation(  # type: ignore[type-var]
    "release.inspect", summary="Inspect an immutable local release handoff", read_only=True
)
def release_inspect_operation(request: ReleaseInspectRequest) -> ReleaseInspectResult:
    """Reuse the retained release parser without adding a publisher path."""
    from infralink.cli import release

    try:
        return release._release_inspect_result(request.release_validation, request.admission)
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation(  # type: ignore[type-var]
    "release.validate-candidate", summary="Validate an immutable release candidate", read_only=True
)
def release_validate_candidate_operation(
    request: ReleaseCandidateRequest,
) -> ReleaseCandidateResult:
    """Validate candidate content without a provider, publisher, or Registry checkout."""
    from infralink.cli import release

    try:
        return release._release_candidate_result(request.candidate)
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation(  # type: ignore[type-var]
    "release.inspect-attestation",
    summary="Inspect an immutable release attestation",
    read_only=True,
)
def release_inspect_attestation_operation(
    request: ReleaseAttestationRequest,
) -> ReleaseAttestationResult:
    """Inspect completion evidence without contacting a publisher."""
    from infralink.cli import release

    try:
        return release._release_attestation_result(request.attestation)
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation(  # type: ignore[type-var]
    "release.render-publisher-request",
    summary="Inspect a release publisher request",
    read_only=True,
)
def release_publisher_request_operation(
    request: ReleasePublisherRequest,
) -> PublisherRequestResult:
    """Read or render a local request without invoking a trusted publisher."""
    from infralink.cli import release

    try:
        return release._release_publisher_request_result(
            request.candidate,
            request.admission,
            request.publisher_request,
        )
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


@operator_surface.operation(  # type: ignore[type-var]
    "doctor", summary="Inspect declared and live observation evidence", read_only=True
)
def doctor_operation(request: DoctorRequest) -> OperationOutcome[DoctorOperationResult]:
    """Run the one doctor evaluator used by retained Click and future composed transports."""
    from infralink.cli.doctor import evaluate_doctor
    from infralink.cli.main import Context

    try:
        sources = load_sources(request)
        context = Context()
        context.registry_path = sources.registry_path
        context.edges_path = sources.edges_path
        context._registry = sources.registry
        context._edges = sources.edges
        inspection = evaluate_doctor(
            context,
            request.observation_plan,
            request.adapter_bindings,
            request.declaration_only,
            request.gatus_url,
            request.gatus_token_env,
            request.target_type,
            request.target_ref,
        )
    except Exception as error:
        _raise_operation_failure(error)
    result = DoctorOperationResult.model_validate(inspection.result.model_dump())
    result._actions = tuple(inspection.actions)
    return OperationOutcome(result, exit_code=inspection.exit_code)


@operator_surface.operation("host.list", summary="List declared hosts", read_only=True)  # type: ignore[type-var]
def host_list(request: HostListRequest) -> HostListResult:
    """Return the registry host list without a Click context."""
    return list_declared_hosts(request)


@operator_surface.operation("host.show", summary="Show one host declaration", read_only=True)  # type: ignore[type-var]
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


@operator_surface.operation("analyze", summary="Generate declared topology artifacts")  # type: ignore[type-var]
def analyze(request: AnalyzeRequest) -> AnalyzeResult:
    """Write deterministic artifacts from the selected Registry checkout."""
    try:
        return analyze_declared_registry(request)
    except OperationError as error:
        if error.code in {"source_not_found", "source_invalid"}:
            details = dict(error.details[0]) if error.details else {}
            details.setdefault("reason", "checkout_root_required")
            raise OperationError(
                "input_load_failed",
                error.message,
                details=(details,),
                fix=error.fix,
            ) from None
        raise
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation("docs", summary="Generate declared topology documentation")  # type: ignore[type-var]
def docs(request: DocsRequest) -> ArtifactResult:
    """Write selected documentation artifacts from the Registry checkout."""
    try:
        return generate_declared_docs(request)
    except OperationError as error:
        if error.code in {"source_not_found", "source_invalid"}:
            details = dict(error.details[0]) if error.details else {}
            details.setdefault("reason", "checkout_root_required")
            raise OperationError(
                "input_load_failed",
                error.message,
                details=(details,),
                fix=error.fix,
            ) from None
        raise
    except Exception as error:
        _raise_operation_failure(error)


@operator_surface.operation("check", summary="Check declared edge health", read_only=True)  # type: ignore[type-var]
def check(request: EdgeCheckRequest) -> OperationOutcome[CheckCommandResult]:
    """Check selected declared edges through the common typed operation."""
    result = check_declared_edges(request)
    return OperationOutcome(result, exit_code=0 if result.healthy else 1)


@operator_surface.operation("resolve", summary="Resolve one declared edge", read_only=True)  # type: ignore[type-var]
def resolve(request: EdgeResolveRequest) -> ResolveResult:
    """Resolve a declared edge through the common typed operation."""
    return resolve_declared_edge(request)


@operator_surface.operation("app.list", summary="List declared applications", read_only=True)  # type: ignore[type-var]
def app_list(request: SourceRequest) -> AppListResult:
    """Return application IDs without a Click context."""
    try:
        return list_declared_apps(request)
    except OperationError as error:
        _raise_app_operation_error(error)


@operator_surface.operation("app.show", summary="Show one declared application", read_only=True)  # type: ignore[type-var]
def app_show(request: AppShowRequest) -> AppShowResult:
    """Return one bounded application view without a Click context."""
    try:
        return show_declared_app(request)
    except OperationError as error:
        _raise_app_operation_error(error)


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


@operator_surface.operation(  # type: ignore[type-var]
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


# The bootstrap-plan builder is intentionally private: it is consumed by the
# host bootstrap and doctor action builders, not exposed as an independent
# public operation that would collide with the doctor command leaf.
doctor_host_bootstrap_plan = _doctor_host_bootstrap_plan


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


def _raise_app_operation_error(error: OperationError) -> NoReturn:
    """Preserve the app family's historical missing-source process exit.

    Agent Surface maps errors to process exits by typed error code.  The
    public envelope keeps the shared ``configuration_required`` code, while
    this private operation code avoids restoring a second Click adapter just
    to retain the legacy app usage exit.
    """
    if error.code == "configuration_required":
        raise OperationError(
            "app_configuration_required",
            error.message,
            details=error.details,
            fix=error.fix,
        ) from None
    raise error
