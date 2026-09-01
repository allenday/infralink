"""HATEOAS actions for declared fleet validation."""

from __future__ import annotations

from typing import Any

from agent_surface.contracts import Action, ActionCollection
from agent_surface.outcomes import ActionProvider
from pydantic import BaseModel

from infralink.fleet.validation import FleetValidationResult
from infralink.operator_surface import FleetValidateRequest


class FleetValidationActionProvider(ActionProvider):
    """Offer one bounded declaration-inspection action for invalid fleets."""

    def actions_for(
        self,
        *,
        operation: str,
        request: BaseModel | None = None,
        result: object | None = None,
        error: Any = None,
    ) -> ActionCollection:
        if (
            operation != "fleet.validate"
            or error is not None
            or not isinstance(request, FleetValidateRequest)
            or not isinstance(result, FleetValidationResult)
            or result.valid
        ):
            return ActionCollection()
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

    def list_actions(
        self, *, cursor: str | None = None, budget: object | None = None
    ) -> ActionCollection:
        return ActionCollection()

    def explain(self, operation: str) -> Action | None:
        return None
