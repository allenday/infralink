"""Infralink's canonical envelope projection for agent-surface adapters."""

from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

import click
from pydantic import BaseModel

from infralink import __version__
from infralink.cli.contracts import Action, CommandContext, Envelope, ErrorDetail
from infralink.cli.errors import ErrorCode, ExitCode

if TYPE_CHECKING:
    from agent_surface import App, Invocation


def mounted_click_command(app: App) -> click.Group:
    """Project an external typed app into the canonical Infralink CLI tree.

    The core executable owns the global ``--output`` switch. Mounted typed
    operations keep YAML as their native default while inheriting an explicit
    root JSON selection, which is how the MCP transport requests JSON.
    """
    from agent_surface.adapters.click import ClickAdapter

    root = ClickAdapter(
        app,
        argv_provider=_mounted_invocation_argv,
        envelope_renderer=InfralinkEnvelopeRenderer(),
        operation_error_exit_code=operation_error_exit_code,
    ).command()
    _inherit_root_output(root)
    return root


def _mounted_invocation_argv() -> tuple[str, ...]:
    # Import lazily: the primary Click module imports this renderer.
    from infralink.cli.main import current_invocation_argv

    argv = current_invocation_argv()
    return ("infralink", *argv) if argv else ("infralink",)


def _root_output_default() -> str:
    context = click.get_current_context(silent=True)
    root = context.find_root() if context is not None else None
    output = getattr(getattr(root, "obj", None), "output", "yaml")
    return output if output in {"yaml", "json"} else "yaml"


def _inherit_root_output(command: click.Command) -> None:
    """Make agent-surface's render option honor Infralink's root contract."""
    for parameter in command.params:
        if isinstance(parameter, click.Option) and parameter.name == "_surface_format":
            parameter.default = _root_output_default
    if isinstance(command, click.Group):
        for child in command.commands.values():
            _inherit_root_output(child)


def operation_error_exit_code(code: str) -> int:
    """Map the typed-operation error taxonomy onto Infralink process exits.

    Agent Surface intentionally supplies only an error code here.  Typed
    operations therefore use an unambiguous contract: malformed command and
    continuation inputs are usage errors; declared-state, schema, and entity
    failures are input errors.  Legacy Click-only call sites that used the
    same ``configuration_required`` label for both categories are not part of
    this adapter boundary.
    """
    try:
        error_code = ErrorCode(code)
    except ValueError:
        return int(ExitCode.INTERNAL_ERROR)
    if error_code in {ErrorCode.USAGE_ERROR, ErrorCode.INVALID_CURSOR}:
        return int(ExitCode.USAGE_ERROR)
    if error_code in {
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.PROVIDER_AUTHENTICATION_FAILED,
        ErrorCode.PROVIDER_AUTHORIZATION_FAILED,
        ErrorCode.PROVIDER_TIMEOUT,
        ErrorCode.RELEASE_PUBLISHER_UNAVAILABLE,
    }:
        return int(ExitCode.PROVIDER_ERROR)
    if error_code is ErrorCode.UNSUPPORTED_PLATFORM:
        return int(ExitCode.UNSUPPORTED_PLATFORM)
    if error_code in {ErrorCode.ARTIFACT_IO_FAILED, ErrorCode.ARTIFACT_RECOVERY_REQUIRED}:
        return int(ExitCode.ARTIFACT_IO_ERROR)
    if error_code is ErrorCode.INTERNAL_ERROR:
        return int(ExitCode.INTERNAL_ERROR)
    return int(ExitCode.INPUT_ERROR)


class InfralinkEnvelopeRenderer:
    """Render typed operations in the published ``infralink.cli/v1`` normal form."""

    output_model = Envelope[Any]

    def render(self, invocation: Invocation) -> BaseModel:
        next_actions = _project_actions(invocation.next_actions, invocation.request)
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
                next_actions=next_actions,
            )
        return Envelope[Any](
            ok=True,
            command=command,
            result=invocation.result,
            next_actions=next_actions,
        )


def _command_context(invocation: Invocation) -> CommandContext:
    path = invocation.operation.name.split(".")
    request = dict(invocation.request or {})
    source_fields = ("registry", "edges")
    if invocation.command is not None:
        raw_tokens = invocation.command.raw
        if not raw_tokens or raw_tokens[0] != "infralink":
            raw_tokens = ("infralink", *raw_tokens)
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
        if token in {"--format", "--output"} and index + 1 < len(tokens):
            return tokens[index + 1]
        if token.startswith(("--format=", "--output=")):
            return token.partition("=")[2]
    return "json" if mcp else "yaml"


def _error_details(details: tuple[dict[str, Any], ...]) -> dict[str, Any]:
    if not details:
        return {}
    if len(details) == 1:
        return details[0]
    return {"items": list(details)}


def _project_actions(actions: Any, request: Mapping[str, Any] | None) -> list[Action]:
    """Project only concrete Agent Surface actions into the v1 action normal form."""
    if actions.truncated:
        raise ValueError("Infralink cannot publish a truncated action frontier")
    projected: list[Action] = []
    for action_value in actions.items:
        if action_value.command is None:
            raise ValueError("Infralink cannot publish an unresolved action template")
        command = _inherit_declared_sources(list(action_value.command), request)
        _validate_action_operation(action_value.operation, command)
        argv = ["infralink", *_declared_source_argv(request), *command]
        projected.append(
            Action(
                rel=action_value.rel,
                argv=argv,
                command=shlex.join(argv),
                description=action_value.description,
                safe=_action_is_read_only(action_value.operation),
            )
        )
    return projected


def _inherit_declared_sources(argv: list[str], request: Mapping[str, Any] | None) -> list[str]:
    """Normalize an action to the invocation's one explicit topology authority."""
    expected = {
        f"--{name.replace('_', '-')}": str(value)
        for name in ("registry", "edges")
        if request is not None and (value := request.get(name)) is not None
    }
    normalized: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        option, separator, inline_value = token.partition("=")
        if option not in {"--registry", "--edges"}:
            normalized.append(token)
            index += 1
            continue
        if separator:
            value = inline_value
            index += 1
        elif index + 1 < len(argv):
            value = argv[index + 1]
            index += 2
        else:
            raise ValueError(f"Infralink action source {option} is missing its value")
        if expected.get(option) != value:
            raise ValueError(f"Infralink action source conflicts with declared {option}")
    if normalized and normalized[0] == "infralink":
        normalized.pop(0)
    return normalized


def _declared_source_argv(request: Mapping[str, Any] | None) -> list[str]:
    if request is None:
        return []
    argv: list[str] = []
    for name in ("registry", "edges"):
        value = request.get(name)
        if value is not None:
            argv.extend((f"--{name.replace('_', '-')}", str(value)))
    return argv


def _validate_action_operation(operation: str | None, argv: list[str]) -> None:
    if operation is None:
        return
    expected = operation.split(".")
    if argv[: len(expected)] != expected:
        raise ValueError("Infralink action command does not match its registered operation")


def _action_is_read_only(operation: str | None) -> bool:
    if operation is None:
        return False
    from agent_surface.operations import UnknownOperationError

    from infralink.operator_surface import operator_surface

    try:
        return bool(operator_surface.operations.describe(operation).read_only)
    except UnknownOperationError:
        return False


__all__ = ["InfralinkEnvelopeRenderer"]
