"""Bounded HATEOAS actions for typed operator operations."""

from __future__ import annotations

from typing import Any

from agent_surface.contracts import Action, ActionCollection
from agent_surface.outcomes import ActionProvider
from pydantic import BaseModel

from infralink.cli.contracts import InfoResult
from infralink.fleet.validation import FleetValidationResult
from infralink.operator_surface import FleetValidateRequest, InfoRequest


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
        if error is not None:
            return ActionCollection()
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
