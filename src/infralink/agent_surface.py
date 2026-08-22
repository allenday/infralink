"""Infralink's canonical envelope projection for agent-surface adapters."""

from __future__ import annotations

import shlex
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from infralink import __version__
from infralink.cli.contracts import CommandContext, Envelope, ErrorDetail

if TYPE_CHECKING:
    from agent_surface import Invocation


class InfralinkEnvelopeRenderer:
    """Render typed operations in the published ``infralink.cli/v1`` normal form."""

    output_model = Envelope[Any]

    def render(self, invocation: Invocation) -> BaseModel:
        if invocation.next_actions.returned:
            raise ValueError("Infralink action projection is required before publishing actions")
        command = _command_context(invocation)
        if invocation.error is not None:
            return Envelope[Any](
                ok=False,
                command=command,
                error=ErrorDetail(
                    code=invocation.error.code,
                    message=invocation.error.message,
                    details=_error_details(invocation.error.details),
                ),
                fix=invocation.error.fix,
                next_actions=[],
            )
        return Envelope[Any](
            ok=True,
            command=command,
            result=invocation.result,
            next_actions=[],
        )


def _command_context(invocation: Invocation) -> CommandContext:
    path = invocation.operation.name.split(".")
    request = dict(invocation.request or {})
    source_fields = ("registry", "edges")
    if invocation.command is not None:
        raw_tokens = invocation.command.raw
        arguments = dict(invocation.command.parsed.args)
    else:
        raw_tokens = _canonical_mcp_command(path, request, source_fields)
        arguments = {name: value for name, value in request.items() if name not in source_fields}
    resolved: dict[str, Any] = {
        "version": __version__,
        "output": _output_format(raw_tokens, mcp=invocation.command is None),
    }
    for name in source_fields:
        value = request.get(name)
        if value is not None:
            resolved[name] = value
    return CommandContext(
        raw=shlex.join(raw_tokens),
        parsed={"path": path, "args": arguments, "flags": _command_flags(raw_tokens)},
        resolved=resolved,
    )


def _canonical_mcp_command(
    path: list[str],
    request: dict[str, Any],
    source_fields: tuple[str, ...],
) -> tuple[str, ...]:
    tokens = ["infralink"]
    for name in source_fields:
        value = request.get(name)
        if value is not None:
            tokens.extend((f"--{name.replace('_', '-')}", str(value)))
    tokens.extend(path)
    return tuple(tokens)


def _command_flags(tokens: tuple[str, ...]) -> list[str]:
    return [token for token in tokens if token.startswith("--")]


def _output_format(tokens: tuple[str, ...], *, mcp: bool) -> str:
    for index, token in enumerate(tokens):
        if token == "--format" and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith("--format="):
            return token.partition("=")[2]
    return "json" if mcp else "yaml"


def _error_details(details: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if not details:
        return {}
    if len(details) == 1:
        return details[0]
    return {"items": list(details)}


__all__ = ["InfralinkEnvelopeRenderer"]
