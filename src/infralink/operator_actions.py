"""Bounded HATEOAS actions for typed operator operations."""

from __future__ import annotations

from typing import Any

from agent_surface.contracts import Action, ActionCollection
from agent_surface.outcomes import ActionProvider
from pydantic import BaseModel

from infralink.app_actions import AppActionProvider
from infralink.cli.contracts import (
    Action as DoctorAction,
)
from infralink.cli.contracts import (
    AnalyzeResult,
    ArtifactResult,
    CheckCommandResult,
    HostListResult,
    InfoResult,
    ResolveResult,
    SecretsInspectResult,
)
from infralink.cli.operation_contracts import (
    HostApplyResult,
    HostLogsResult,
    HostStatusResult,
    HostVerifierResult,
)
from infralink.fleet.validation import FleetValidationResult
from infralink.operator_operations.analyze import AnalyzeRequest
from infralink.operator_operations.docs import DocsRequest
from infralink.operator_operations.edge_health import EdgeCheckRequest, EdgeResolveRequest
from infralink.operator_surface import (
    DoctorOperationResult,
    FleetValidateRequest,
    HostApplyRequest,
    HostBootstrapOperationResult,
    HostBootstrapRequest,
    HostCreateRequest,
    HostCreateResult,
    HostLogsRequest,
    HostTargetRequest,
    InfoRequest,
    RegistryHostGetOperationResult,
    RegistryHostGetRequest,
    RegistryHostPatchOperationResult,
    RegistryHostPatchRequest,
    ReleaseCandidateRequest,
    ReleaseInspectRequest,
    ReleasePublisherRequest,
    SecretsInspectRequest,
)


class OperatorActionProvider(ActionProvider):
    """Offer only concrete, bounded follow-ups from operator read results."""

    def actions_for(
        self,
        *,
        operation: str,
        request: BaseModel | None = None,
        result: object | None = None,
        error: Any = None,
    ) -> ActionCollection:
        if operation == "doctor" and isinstance(result, DoctorOperationResult):
            return _doctor_actions(result._actions)
        if operation == "doctor" and error is not None:
            return _doctor_error_actions(error)
        if (
            error is not None
            and operation == "docs"
            and isinstance(request, DocsRequest)
            and getattr(error, "code", None) == "entity_not_found"
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="list",
                        description="List host declarations",
                        command=("host", "list"),
                        operation="host.list",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            error is not None
            and operation == "resolve"
            and isinstance(request, EdgeResolveRequest)
            and getattr(error, "code", None) in {"entity_not_found", "input_load_failed"}
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="list",
                        description="List edge records",
                        command=("edge", "list"),
                        operation="edge.list",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            error is not None
            and operation == "secrets.inspect"
            and isinstance(request, SecretsInspectRequest)
            and getattr(error, "code", None) == "entity_not_found"
            and _error_entity_type(error) == "secret_reference"
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="inspect",
                        description="Inspect declared secret references",
                        command=("secrets", "inspect"),
                        operation="secrets.inspect",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            error is not None
            and operation == "host.bootstrap"
            and isinstance(request, HostBootstrapRequest)
            and getattr(error, "code", None) == "configuration_required"
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="inspect",
                        description="Inspect the target host declaration",
                        command=("host", "show", request.host_id),
                        operation="host.show",
                    ),
                ),
                total=1,
                returned=1,
            )
        if error is not None and getattr(error, "code", None) == "usage_error":
            return ActionCollection(
                items=(
                    Action(
                        rel="help",
                        description="Show command usage",
                        command=("help", "--path", operation),
                        operation="help",
                    ),
                ),
                total=1,
                returned=1,
            )
        if error is not None and operation.startswith("release."):
            return ActionCollection(
                items=(
                    Action(
                        rel="help",
                        description="Show release command usage",
                        command=("help", "--path", operation),
                        operation="help",
                    ),
                ),
                total=1,
                returned=1,
            )
        if error is not None:
            return ActionCollection()
        if (
            operation == "release.inspect"
            and isinstance(request, ReleaseInspectRequest)
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="inspect",
                        description="Reinspect this immutable release handoff",
                        command=(
                            "release",
                            "inspect",
                            "--release-validation",
                            str(request.release_validation),
                            "--admission",
                            str(request.admission),
                        ),
                        operation="release.inspect",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "release.validate-candidate"
            and isinstance(request, ReleaseCandidateRequest)
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="render-publisher-request",
                        description="Render the explicit trusted-publisher handoff after selecting local admission policy",
                        command_template=(
                            "release",
                            "render-publisher-request",
                            "--candidate",
                            str(request.candidate),
                            "--admission",
                            "{admission}",
                        ),
                        operation="release.render-publisher-request",
                        slots={
                            "admission": {
                                "type": "string",
                                "required": True,
                                "source": "local release admission policy path",
                            }
                        },
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "release.render-publisher-request"
            and isinstance(request, ReleasePublisherRequest)
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="inspect-attestation",
                        description="Inspect the immutable publisher attestation after the trusted publisher completes",
                        command_template=(
                            "release",
                            "inspect-attestation",
                            "--attestation",
                            "{attestation}",
                        ),
                        operation="release.inspect-attestation",
                        slots={
                            "attestation": {
                                "type": "string",
                                "required": True,
                                "source": "trusted publisher completion record path",
                            }
                        },
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "analyze"
            and isinstance(request, AnalyzeRequest)
            and isinstance(result, AnalyzeResult)
        ):
            return _analyze_actions(request, result)
        if (
            operation == "docs"
            and isinstance(request, DocsRequest)
            and isinstance(result, ArtifactResult)
        ):
            return _docs_actions(request, result)
        if (
            operation == "host.create"
            and isinstance(request, HostCreateRequest)
            and isinstance(result, HostCreateResult)
            and result.mode == "written"
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="show",
                        description="Show the created host declaration",
                        command=("host", "show", result.host_id),
                        operation="host.show",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "registry.host.get"
            and isinstance(request, RegistryHostGetRequest)
            and isinstance(result, RegistryHostGetOperationResult)
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="patch",
                        description="Preview a typed host declaration mutation",
                        command_template=(
                            "registry",
                            "host",
                            "patch",
                            result.host.id,
                            "--registry",
                            str(result._checkout),
                            "--set",
                            "{assignment}",
                        ),
                        operation="registry.host.patch",
                        slots={
                            "assignment": {
                                "type": "string",
                                "required": True,
                                "source": "operator.input",
                            }
                        },
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "registry.host.patch"
            and isinstance(request, RegistryHostPatchRequest)
            and isinstance(result, RegistryHostPatchOperationResult)
            and not request.write
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="write",
                        description="Write this reviewed host declaration mutation",
                        command=(
                            "registry",
                            "host",
                            "patch",
                            result.host.id,
                            "--registry",
                            str(result._checkout),
                            *sum((("--set", assignment) for assignment in request.assignments), ()),
                            "--write",
                        ),
                        operation="registry.host.patch",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "secrets.inspect"
            and isinstance(request, SecretsInspectRequest)
            and isinstance(result, SecretsInspectResult)
        ):
            return _secrets_inspect_actions(request, result)
        if operation == "host.list" and isinstance(result, HostListResult) and result.items:
            return ActionCollection(
                items=(
                    Action(
                        rel="show",
                        description="Show one host declaration",
                        command_template=("host", "show", "{host_id}"),
                        operation="host.show",
                        slots={
                            "host_id": {
                                "type": "string",
                                "required": True,
                                "source": "result.items[]",
                            }
                        },
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "host.apply"
            and isinstance(request, HostApplyRequest)
            and isinstance(result, HostApplyResult)
        ):
            return _host_apply_actions(request, result)
        if (
            operation == "host.bootstrap"
            and isinstance(request, HostBootstrapRequest)
            and isinstance(result, HostBootstrapOperationResult)
        ):
            return _host_bootstrap_actions(request, result)
        if (
            operation == "host.status"
            and isinstance(request, HostTargetRequest)
            and isinstance(result, HostStatusResult)
        ):
            return _host_logs_action_for_target(result.target.id)
        if (
            operation == "host.logs"
            and isinstance(request, HostLogsRequest)
            and isinstance(result, HostLogsResult)
        ):
            return _host_status_action_for_target(result.target.id)
        if (
            operation == "host.verifier"
            and isinstance(request, HostTargetRequest)
            and isinstance(result, HostVerifierResult)
        ):
            return _host_status_action_for_target(result.target.id)
        if (
            operation == "fleet.validate"
            and isinstance(request, FleetValidateRequest)
            and isinstance(result, FleetValidationResult)
            and not result.valid
        ):
            command = ["fleet", "validate"]
            if request.host is not None:
                command.extend(("--host", request.host))
            if request.strict:
                command.append("--strict")
            if request.live:
                command.append("--live")
            return ActionCollection(
                items=(
                    Action(
                        rel="inspect-declaration",
                        description="Inspect the bounded declaration diagnostics before controller reconciliation",
                        command=tuple(command),
                        operation="fleet.validate",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "check"
            and isinstance(request, EdgeCheckRequest)
            and isinstance(result, CheckCommandResult)
        ):
            return _check_actions(request, result)
        if (
            operation == "resolve"
            and isinstance(request, EdgeResolveRequest)
            and isinstance(result, ResolveResult)
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="check",
                        description="Check this edge",
                        command=("check", "--edge", request.edge_id),
                        operation="check",
                    ),
                ),
                total=1,
                returned=1,
            )
        if (
            operation == "info"
            and isinstance(request, InfoRequest)
            and isinstance(result, InfoResult)
        ):
            return ActionCollection(
                items=(
                    Action(
                        rel="list",
                        description="List all hosts",
                        command=("host", "list"),
                        operation="host.list",
                    ),
                    Action(
                        rel="list",
                        description="List all edges",
                        command=("edge", "list"),
                        operation="edge.list",
                    ),
                ),
                total=2,
                returned=2,
            )
        return ActionCollection()

    def list_actions(
        self, *, cursor: str | None = None, budget: object | None = None
    ) -> ActionCollection:
        return ActionCollection()

    def explain(self, operation: str) -> Action | None:
        return None


def _doctor_actions(actions: tuple[DoctorAction, ...]) -> ActionCollection:
    """Project evaluator-owned, bounded doctor repairs without re-inferring state."""
    items: list[Action] = []
    for action in actions:
        if not action.argv or action.argv[0] != "infralink":
            continue
        items.append(
            Action(
                rel=action.rel,
                description=action.description,
                command=tuple(action.argv[1:]),
            )
        )
    return ActionCollection(items=tuple(items), total=len(items), returned=len(items))


def _doctor_error_actions(error: Any) -> ActionCollection:
    """Offer only the registered repair frontier retained after adapter redaction."""
    details = error.details[0] if getattr(error, "details", ()) else {}
    entity_type = details.get("entity_type") if isinstance(details, dict) else None
    if entity_type in {"host", "service", "edge"}:
        item = Action(
            rel="list",
            description=f"List {entity_type} records",
            command=(str(entity_type), "list"),
            operation=f"{entity_type}.list",
        )
    else:
        item = Action(
            rel="help",
            description="Show doctor usage",
            command=("help", "--path", "doctor"),
            operation="help",
        )
    return ActionCollection(items=(item,), total=1, returned=1)


def _error_entity_type(error: Any) -> str | None:
    """Read the one retained domain discriminator from typed error details."""
    details: tuple[object, ...] = tuple(getattr(error, "details", ()))
    if len(details) != 1:
        return None
    detail = details[0]
    if not isinstance(detail, dict):
        return None
    entity_type = detail.get("entity_type")
    return entity_type if isinstance(entity_type, str) else None


def _secrets_inspect_actions(
    request: SecretsInspectRequest,
    result: SecretsInspectResult,
) -> ActionCollection:
    """Preserve bounded secret-inspection navigation without provider access."""
    actions: list[Action] = []
    for collection, page, source in (
        (
            "references",
            result.references.page,
            "result.references.page.next_cursor",
        ),
        (
            "locations",
            result.locations.page,
            "result.locations.page.next_cursor",
        ),
    ):
        if page.next_cursor is None:
            continue
        command = ["secrets", "inspect"]
        if request.requested_ref is not None:
            command.extend(("--ref", request.requested_ref))
        command.extend(
            ("--collection", collection, "--cursor", "{cursor}", "--limit", str(request.limit))
        )
        actions.append(
            Action(
                rel="continue",
                description=f"Continue secret {collection}",
                command_template=tuple(command),
                operation="secrets.inspect",
                slots={
                    "cursor": {
                        "type": "string",
                        "required": True,
                        "source": source,
                    }
                },
            )
        )
    for reference in result.references.items:
        if not reference.locations_truncated:
            continue
        actions.append(
            Action(
                rel="inspect",
                description="Inspect all declaration locations",
                command=(
                    "secrets",
                    "inspect",
                    "--ref",
                    reference.ref,
                    "--collection",
                    "locations",
                ),
                operation="secrets.inspect",
            )
        )
    return ActionCollection(items=tuple(actions), total=len(actions), returned=len(actions))


def _analyze_actions(request: AnalyzeRequest, result: AnalyzeResult) -> ActionCollection:
    """Continue either bounded analyze result collection through the same operation."""
    actions: list[Action] = []
    for collection, page, source in (
        (
            "diagnostics",
            result.analysis.diagnostics.page,
            "result.analysis.diagnostics.page.next_cursor",
        ),
        ("artifacts", result.artifacts.page, "result.artifacts.page.next_cursor"),
    ):
        if page.next_cursor is None:
            continue
        command = ["analyze", "--output", request.output.as_posix()]
        if not request.include_edges:
            command.append("--no-include-edges")
        if not request.include_diagram:
            command.append("--no-include-diagram")
        if not request.include_monitoring:
            command.append("--no-include-monitoring")
        command.extend(
            ("--collection", collection, "--cursor", "{cursor}", "--limit", str(request.limit))
        )
        actions.append(
            Action(
                rel="continue",
                description=f"Continue analyze {collection}",
                command_template=tuple(command),
                operation="analyze",
                slots={
                    "cursor": {
                        "type": "string",
                        "required": True,
                        "source": source,
                    }
                },
            )
        )
    return ActionCollection(items=tuple(actions), total=len(actions), returned=len(actions))


def _docs_actions(request: DocsRequest, result: ArtifactResult) -> ActionCollection:
    """Continue the bounded docs artifact listing through the same operation."""
    page = result.artifacts.page
    if page.next_cursor is None:
        return ActionCollection()
    command = [
        "docs",
        "--output",
        request.output.as_posix(),
        "--document-format",
        request.document_format,
    ]
    if request.host is not None:
        command.extend(("--host", request.host))
    if request.index_only:
        command.append("--index-only")
    command.extend(
        ("--collection", "artifacts", "--cursor", "{cursor}", "--limit", str(request.limit))
    )
    return ActionCollection(
        items=(
            Action(
                rel="continue",
                description="Continue docs artifacts",
                command_template=tuple(command),
                operation="docs",
                slots={
                    "cursor": {
                        "type": "string",
                        "required": True,
                        "source": "result.artifacts.page.next_cursor",
                    }
                },
            ),
        ),
        total=1,
        returned=1,
    )


def _check_actions(request: EdgeCheckRequest, result: CheckCommandResult) -> ActionCollection:
    """Return failure repair and bounded continuation actions for one health result."""
    actions: list[Action] = []
    if result._failed_edge_id is not None:
        actions.extend(
            (
                Action(
                    rel="show",
                    description="Inspect the failed edge",
                    command=("edge", "show", result._failed_edge_id),
                    operation="edge.show",
                ),
                Action(
                    rel="resolve",
                    description="Resolve the failed edge target",
                    command=("resolve", result._failed_edge_id),
                    operation="resolve",
                ),
            )
        )
    if result.checks.page.next_cursor is not None:
        command: list[str] = ["check"]
        for edge_id in request.edge_ids:
            command.extend(("--edge", edge_id))
        if request.edge_type is not None:
            command.extend(("--type", request.edge_type))
        if request.criticality is not None:
            command.extend(("--criticality", request.criticality))
        if request.critical_only:
            command.append("--critical-only")
        command.extend(
            (
                "--timeout",
                str(request.timeout),
                "--collection",
                "checks",
                "--cursor",
                "{cursor}",
                "--limit",
                str(request.limit),
            )
        )
        actions.append(
            Action(
                rel="continue",
                description="Continue checks",
                command_template=tuple(command),
                operation="check",
                slots={
                    "cursor": {
                        "type": "string",
                        "required": True,
                        "source": "result.checks.page.next_cursor",
                    }
                },
            )
        )
    return ActionCollection(items=tuple(actions), total=len(actions), returned=len(actions))


def _host_apply_actions(request: HostApplyRequest, result: HostApplyResult) -> ActionCollection:
    """Return only registered, source-inheriting host operation follow-ups."""
    target_id = result.target.id
    if target_id is None:
        return ActionCollection()
    if result.dry_run:
        return ActionCollection(
            items=(
                Action(
                    rel="apply",
                    description="Submit this declared host reconcile",
                    command=("host", "apply", target_id),
                    operation="host.apply",
                ),
            ),
            total=1,
            returned=1,
        )
    if result.dispatch is not None and result.dispatch.status != "accepted":
        return _host_status_and_logs_actions(target_id)
    if result.operation is not None and result.operation.state in {"queued", "applying"}:
        return ActionCollection(
            items=(
                Action(
                    rel="status",
                    description="Check host reconcile progress",
                    command=("operation", "status", result.operation.id),
                    operation="operation.status",
                ),
            ),
            total=1,
            returned=1,
        )
    return _host_status_action(target_id)


def _host_bootstrap_actions(
    request: HostBootstrapRequest, result: HostBootstrapOperationResult
) -> ActionCollection:
    """Offer a typed stdin handoff only when bootstrap can safely automate it."""
    from infralink.operator_operations.host_bootstrap import _bootstrap_apply_handoff_is_safe

    target_id = result.result.host.id
    if target_id is None:
        return ActionCollection()
    reinspect = Action(
        rel="reinspect-readiness",
        description="Reinspect live host readiness",
        command=("host", "bootstrap", target_id, "--ssh-host", request.ssh_host),
        operation="host.bootstrap",
    )
    if request.bws_token is not None or not _bootstrap_apply_handoff_is_safe(
        result.result.readiness
    ):
        return ActionCollection(items=(reinspect,), total=1, returned=1)
    apply = Action(
        rel="apply",
        description="Apply declared bootstrap; provide the BWS machine token on standard input",
        command=(
            "host",
            "bootstrap",
            target_id,
            "--ssh-host",
            request.ssh_host,
            "--bws-token-stdin",
            "--apply",
        ),
        operation="host.bootstrap",
    )
    return ActionCollection(items=(reinspect, apply), total=2, returned=2)


def _host_status_action(target_id: str) -> ActionCollection:
    return ActionCollection(
        items=(
            Action(
                rel="status",
                description="Inspect the target timer and latest reconcile result",
                command=("host", "status", target_id),
                operation="host.status",
            ),
        ),
        total=1,
        returned=1,
    )


def _host_status_action_for_target(target_id: str | None) -> ActionCollection:
    if target_id is None:
        return ActionCollection()
    return _host_status_action(target_id)


def _host_logs_action(target_id: str) -> ActionCollection:
    return ActionCollection(
        items=(
            Action(
                rel="logs",
                description="Inspect bounded evidence from the target's latest reconcile run",
                command=("host", "logs", target_id, "--last-run"),
                operation="host.logs",
            ),
        ),
        total=1,
        returned=1,
    )


def _host_logs_action_for_target(target_id: str | None) -> ActionCollection:
    if target_id is None:
        return ActionCollection()
    return _host_logs_action(target_id)


def _host_status_and_logs_actions(target_id: str) -> ActionCollection:
    status = _host_status_action(target_id)
    logs = _host_logs_action(target_id)
    return ActionCollection(
        items=(*status.items, *logs.items),
        total=2,
        returned=2,
    )


class InfralinkActionProvider(ActionProvider):
    """Route actions from the one public operation registry.

    The application read family retains its bounded navigation semantics;
    every other operation uses the established operator action catalog.  This
    is action policy only, not another App or transport surface.
    """

    def __init__(self) -> None:
        self._operator = OperatorActionProvider()
        self._app = AppActionProvider()

    def actions_for(
        self,
        *,
        operation: str,
        request: BaseModel | None = None,
        result: object | None = None,
        error: Any = None,
    ) -> ActionCollection:
        provider = self._app if operation.startswith("app.") else self._operator
        return provider.actions_for(
            operation=operation,
            request=request,
            result=result,
            error=error,
        )

    def list_actions(
        self, *, cursor: str | None = None, budget: object | None = None
    ) -> ActionCollection:
        del cursor, budget
        return ActionCollection()

    def explain(self, operation: str) -> Action | None:
        del operation
        return None
