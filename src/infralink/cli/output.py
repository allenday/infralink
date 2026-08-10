from __future__ import annotations

import shlex
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import fields, is_dataclass
from typing import Any, cast, overload

from pydantic import BaseModel

from infralink.cli.actions import redact_argv, render_action
from infralink.cli.contracts import Action, CommandContext, Envelope, ErrorDetail
from infralink.cli.errors import CliFailure

_REDACTED = "[REDACTED]"
_SENSITIVE_KEYS = {"access_token", "password", "password_env", "token"}


_INHERITABLE_SOURCE_OPTIONS = frozenset(
    {"--registry", "--edges", "--observation-plan", "--adapter-bindings"}
)
_SOURCE_OPTION_ALIASES = {"-r": "--registry", "-e": "--edges"}


def _inherited_source_options(context: CommandContext) -> frozenset[str]:
    supplied = {
        _SOURCE_OPTION_ALIASES.get(flag.split("=", 1)[0], flag.split("=", 1)[0])
        for flag in context.parsed.get("flags", [])
    }
    return frozenset(option for option in _INHERITABLE_SOURCE_OPTIONS if option not in supplied)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _sanitize_value(value.model_dump(mode="json"))
    if is_dataclass(value) and not isinstance(value, type):
        return _sanitize_value({field.name: getattr(value, field.name) for field in fields(value)})
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("unsupported command context value: non-string mapping key")
        return {
            key: (
                _REDACTED
                if key.casefold().replace("-", "_") in _SENSITIVE_KEYS
                else _sanitize_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported command context value: {type(value).__name__}")


def command_context(
    argv: list[str],
    path: list[str],
    args: dict[str, Any],
    flags: list[str],
    resolved: dict[str, Any],
) -> CommandContext:
    return CommandContext(
        raw=shlex.join(redact_argv(argv)),
        parsed={
            "path": deepcopy(path),
            "args": _sanitize_value(args),
            "flags": deepcopy(flags),
        },
        resolved=_sanitize_value(resolved),
    )


@overload
def ok_envelope(
    context: CommandContext,
    result: Any,
    next_actions: list[Action],
    status: str = "ok",
) -> dict[str, Any]: ...


@overload
def ok_envelope(
    *,
    command: str,
    result: Any,
    next_actions: list[dict[str, str]],
    status: str = "ok",
) -> dict[str, Any]: ...


@overload
def ok_envelope(
    context: str,
    result: Any,
    next_actions: list[dict[str, str]],
    status: str = "ok",
) -> dict[str, Any]: ...


def ok_envelope(
    context: CommandContext | str | None = None,
    result: Any = None,
    next_actions: list[Action] | list[dict[str, str]] | None = None,
    status: str = "ok",
    *,
    command: str | None = None,
) -> dict[str, Any]:
    if command is not None:
        if context is not None:
            raise TypeError("provide either context or command, not both")
        context = command
    if context is None or next_actions is None:
        raise TypeError("context and next_actions are required")

    if isinstance(context, str):
        return {
            "status": status,
            "ok": status == "ok",
            "command": context,
            "result": result,
            "next_actions": next_actions,
        }

    envelope = Envelope[Any](
        ok=True,
        command=context,
        result=result,
        next_actions=cast(list[Action], next_actions),
    )
    payload = envelope.model_dump(mode="json")
    payload["next_actions"] = [
        render_action(item, context.resolved, inherited_options=_inherited_source_options(context))
        for item in cast(list[Action], next_actions)
    ]
    payload.pop("error", None)
    payload.pop("fix", None)
    return payload


@overload
def error_envelope(
    context: CommandContext,
    failure: CliFailure,
    code: None = None,
    fix: None = None,
    next_actions: None = None,
) -> dict[str, Any]: ...


@overload
def error_envelope(
    *,
    command: str,
    message: str,
    code: str,
    fix: str,
    next_actions: list[dict[str, str]],
) -> dict[str, Any]: ...


@overload
def error_envelope(
    context: str,
    failure: str,
    code: str,
    fix: str,
    next_actions: list[dict[str, str]],
) -> dict[str, Any]: ...


def error_envelope(
    context: CommandContext | str | None = None,
    failure: CliFailure | str | None = None,
    code: str | None = None,
    fix: str | None = None,
    next_actions: list[dict[str, str]] | None = None,
    *,
    command: str | None = None,
    message: str | None = None,
) -> dict[str, Any]:
    if command is not None:
        if context is not None:
            raise TypeError("provide either context or command, not both")
        context = command
    if message is not None:
        if failure is not None:
            raise TypeError("provide either failure or message, not both")
        failure = message
    if context is None:
        raise TypeError("context is required")

    if isinstance(context, str):
        if not isinstance(failure, str) or code is None or fix is None or next_actions is None:
            raise TypeError("legacy error envelope requires message, code, fix, and next_actions")
        return {
            "status": "error",
            "ok": False,
            "command": context,
            "error": {"message": failure, "code": code},
            "fix": fix,
            "next_actions": next_actions,
        }

    if not isinstance(failure, CliFailure):
        raise TypeError("v1 error envelope requires a CliFailure")
    envelope = Envelope[Any](
        ok=False,
        command=context,
        error=ErrorDetail(
            code=failure.code.value,
            message=failure.message,
            details=failure.details,
        ),
        fix=failure.fix,
        next_actions=failure.next_actions,
    )
    payload = envelope.model_dump(mode="json")
    payload["next_actions"] = [
        render_action(item, context.resolved, inherited_options=_inherited_source_options(context))
        for item in failure.next_actions
    ]
    payload.pop("result", None)
    return payload
