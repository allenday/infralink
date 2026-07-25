from __future__ import annotations

import shlex
from typing import Any, cast, overload

from infralink.cli.contracts import Action, CommandContext, Envelope, ErrorDetail
from infralink.cli.errors import CliFailure

SENSITIVE_OPTIONS = {
    "--access-token",
    "--password",
    "--password-env",
    "--token",
}


def redact_argv(argv: list[str]) -> list[str]:
    redacted: list[str] = []
    redact_next = False
    for value in argv:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue

        option, separator, _ = value.partition("=")
        if option in SENSITIVE_OPTIONS:
            redacted.append(f"{option}=[REDACTED]" if separator else option)
            redact_next = not separator
            continue

        redacted.append(value)
    return redacted


def command_context(
    argv: list[str],
    path: list[str],
    args: dict[str, Any],
    flags: list[str],
    resolved: dict[str, Any],
) -> CommandContext:
    return CommandContext(
        raw=shlex.join(redact_argv(argv)),
        parsed={"path": list(path), "args": dict(args), "flags": list(flags)},
        resolved=dict(resolved),
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
    context: str,
    result: Any,
    next_actions: list[dict[str, str]],
    status: str = "ok",
) -> dict[str, Any]: ...


def ok_envelope(
    context: CommandContext | str,
    result: Any,
    next_actions: list[Action] | list[dict[str, str]],
    status: str = "ok",
) -> dict[str, Any]:
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
    return envelope.model_dump(mode="json", exclude_none=True)


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
    context: str,
    failure: str,
    code: str,
    fix: str,
    next_actions: list[dict[str, str]],
) -> dict[str, Any]: ...


def error_envelope(
    context: CommandContext | str,
    failure: CliFailure | str,
    code: str | None = None,
    fix: str | None = None,
    next_actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
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
    return envelope.model_dump(mode="json", exclude_none=True)
