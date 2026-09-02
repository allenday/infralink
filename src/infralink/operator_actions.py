"""Bounded HATEOAS actions for typed operator operations."""

from __future__ import annotations

from typing import Any

from agent_surface.contracts import Action, ActionCollection
from agent_surface.outcomes import ActionProvider
from pydantic import BaseModel

from infralink.cli.contracts import InfoResult
from infralink.cli.operation_contracts import (
    HostApplyResult,
    HostLogsResult,
    HostStatusResult,
    HostVerifierResult,
)
from infralink.fleet.validation import FleetValidationResult
from infralink.operator_surface import (
    FleetValidateRequest,
    HostApplyRequest,
    HostBootstrapOperationResult,
    HostBootstrapRequest,
    HostCreateRequest,
    HostCreateResult,
    HostLogsRequest,
    HostTargetRequest,
    InfoRequest,
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
        if error is not None:
            return ActionCollection()
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


def _host_apply_actions(
    request: HostApplyRequest, result: HostApplyResult
) -> ActionCollection:
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
