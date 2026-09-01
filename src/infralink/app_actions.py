"""Bounded contextual actions for the public application read registry."""

from __future__ import annotations

from typing import Any

from agent_surface.contracts import Action as SurfaceAction
from agent_surface.contracts import ActionCollection
from agent_surface.outcomes import ActionProvider
from pydantic import BaseModel

from infralink.cli.contracts import AppListResult, AppShowResult
from infralink.operator_operations.topology import AppShowRequest


class AppActionProvider(ActionProvider):
    """Publish only concrete, bounded follow-ups for app read results."""

    def actions_for(
        self,
        *,
        operation: str,
        request: BaseModel | None = None,
        result: object | None = None,
        error: Any = None,
    ) -> ActionCollection:
        actions = [_help_action(operation)]
        if error is not None:
            return ActionCollection(items=tuple(actions), total=len(actions), returned=len(actions))
        if operation == "app.list" and isinstance(result, AppListResult):
            actions.extend(_show_actions(result))
        elif (
            operation == "app.show"
            and isinstance(request, AppShowRequest)
            and isinstance(result, AppShowResult)
        ):
            actions.extend(_continuation_actions(request, result))
        return ActionCollection(items=tuple(actions), total=len(actions), returned=len(actions))

    def list_actions(
        self, *, cursor: str | None = None, budget: object | None = None
    ) -> ActionCollection:
        return ActionCollection()

    def explain(self, operation: str) -> SurfaceAction | None:
        return None


def _help_action(operation: str) -> SurfaceAction:
    path = tuple(operation.split("."))
    return SurfaceAction(
        rel="help",
        description=f"Show {' '.join(path)} help",
        command=("help", *path),
        operation=operation,
    )


def _show_actions(result: AppListResult) -> list[SurfaceAction]:
    if not result.items:
        return []
    return [
        SurfaceAction(
            rel="show",
            description="Show one application",
            command_template=("app", "show", "{app_id}"),
            operation="app.show",
            slots={
                "app_id": {
                    "type": "string",
                    "required": True,
                    "source": "result.items[]",
                }
            },
        )
    ]


def _continuation_actions(request: AppShowRequest, result: AppShowResult) -> list[SurfaceAction]:
    actions: list[SurfaceAction] = []
    for collection in ("services", "edges"):
        page = getattr(result, collection).page
        if page.next_cursor is None:
            continue
        actions.append(
            SurfaceAction(
                rel="continue",
                description=f"Continue {collection}",
                command=(
                    "app",
                    "show",
                    request.app_id,
                    "--collection",
                    collection,
                    "--cursor",
                    page.next_cursor,
                    "--limit",
                    str(request.limit),
                ),
                operation="app.show",
            )
        )
    return actions
