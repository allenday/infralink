"""Infralink's canonical envelope projection for agent-surface adapters."""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Any

import agent_surface.adapters.click as agent_surface_click
import click
from agent_surface.adapters.click import ClickAdapter
from agent_surface.budgets import OutputBudget
from agent_surface.outcomes import ActionProvider
from agent_surface.rendering import RenderOptions
from pydantic import BaseModel

from infralink import __version__
from infralink.cli.contracts import Action, Binding, CommandContext, Envelope, ErrorDetail
from infralink.cli.errors import ErrorCode, ExitCode
from infralink.operator_config import OperatorConfigError, configured_registry

if TYPE_CHECKING:
    from agent_surface import App, Invocation


_compact_mounted_json: ContextVar[bool] = ContextVar("compact_mounted_json", default=False)
_agent_surface_render = agent_surface_click.render  # type: ignore[attr-defined]


def _render_mounted_cli(value: Any, *, options: Any = None) -> str:
    """Preserve Infralink's one-line JSON CLI envelope for mounted operations."""
    rendered = _agent_surface_render(value, options=options)
    if _compact_mounted_json.get() and options is not None and options.format == "json":
        return json.dumps(json.loads(rendered), separators=(",", ":")) + "\n"
    return rendered


agent_surface_click.render = _render_mounted_cli  # type: ignore[attr-defined]


class _MountedClickAdapter(ClickAdapter):
    """Carry the root's canonical topology selections into mounted operations."""

    def _payload(self, context: click.Context, plan: Any, params: dict[str, Any]) -> dict[str, Any]:
        payload = super()._payload(context, plan, params)
        for field in plan.fields:
            if field.source != "argv" or field.name not in {"registry", "edges"}:
                continue
            value = params.get(field.name)
            if value is not None:
                payload[field.name] = value
        return payload

    def _invoke(self, context: click.Context, plan: Any, params: dict[str, Any]) -> None:
        token = _compact_mounted_json.set(True)
        try:
            super()._invoke(context, plan, params)
        finally:
            _compact_mounted_json.reset(token)


def mounted_click_command(
    app: App,
    *,
    action_provider: ActionProvider | None = None,
    envelope_renderer: InfralinkEnvelopeRenderer | None = None,
    render_options: RenderOptions | None = None,
    operation_error_exit_code_override: Callable[[str], int] | None = None,
) -> click.Group:
    """Project an external typed app into the canonical Infralink CLI tree.

    The core executable owns the global ``--output`` switch. Mounted typed
    operations keep YAML as their native default while inheriting an explicit
    root JSON selection, which is how the MCP transport requests JSON.
    """
    root = _MountedClickAdapter(
        app,
        argv_provider=_mounted_invocation_argv,
        action_provider=action_provider,
        envelope_renderer=envelope_renderer or InfralinkEnvelopeRenderer(),
        render_options=render_options,
        operation_error_exit_code=operation_error_exit_code_override or operation_error_exit_code,
    ).command()
    _inherit_root_output(root)
    return root


def app_render_options() -> RenderOptions:
    """Preserve the legacy bounded application query response capacity."""
    return RenderOptions(budget=OutputBudget(max_items=1000))


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


def _root_source_default(name: str) -> Any:
    context = click.get_current_context(silent=True)
    root = context.find_root() if context is not None else None
    attribute = {"registry": "registry_path", "edges": "edges_path"}.get(name)
    return getattr(getattr(root, "obj", None), attribute, None) if attribute is not None else None


def _inherit_root_output(command: click.Command) -> None:
    """Remove duplicate selector authority from a mounted command tree."""
    for parameter in command.params:
        if not isinstance(parameter, click.Option):
            continue
        if parameter.name == "_surface_format":
            parameter.default = _root_output_default
        elif parameter.name == "_surface_yaml_style":
            pass
        elif parameter.name in {"registry", "edges"}:
            parameter.default = lambda name=parameter.name: _root_source_default(name)
        else:
            continue
        parameter.hidden = True
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
    if code in {
        "configuration_required",
        "input_load_failed",
        "source_invalid",
        "source_not_found",
    }:
        return int(ExitCode.INPUT_ERROR)
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

    output_model: type[BaseModel] = Envelope[Any]

    def render(self, invocation: Invocation) -> BaseModel:
        action_sources = self._action_sources(invocation.request)
        if invocation.operation.name == "info":
            # Info's follow-ups inspect both topology documents. Preserve the
            # effective companion edge source even when callers selected only
            # a checkout root.
            action_sources = _info_action_sources(invocation.request)
        next_actions = _project_actions(
            invocation.next_actions,
            action_sources,
            allow_templates=self._allow_action_templates(),
        )
        command = _command_context(invocation)
        if invocation.error is not None:
            return Envelope[Any](
                ok=False,
                command=command,
                error=ErrorDetail(
                    code=self._error_code(invocation),
                    message=invocation.error.message,
                    details=self._error_details(invocation),
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

    @staticmethod
    def _error_code(invocation: Invocation) -> str:
        assert invocation.error is not None
        return invocation.error.code

    @staticmethod
    def _error_details(invocation: Invocation) -> dict[str, Any]:
        assert invocation.error is not None
        return _error_details(invocation.error.details)

    @staticmethod
    def _action_sources(request: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return request

    @staticmethod
    def _allow_action_templates() -> bool:
        return False


class AppEnvelopeRenderer(InfralinkEnvelopeRenderer):
    """Preserve the legacy source-load envelope for the public app family only."""

    @staticmethod
    def _error_code(invocation: Invocation) -> str:
        assert invocation.error is not None
        return _canonical_error_code(invocation.error.code)

    @staticmethod
    def _error_details(invocation: Invocation) -> dict[str, Any]:
        return _canonical_error_details(invocation)

    @staticmethod
    def _action_sources(request: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
        return _effective_source_values(request)

    @staticmethod
    def _allow_action_templates() -> bool:
        return True


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
    effective_sources = _effective_source_values(request)
    resolved: dict[str, Any] = {
        "version": __version__,
        "cwd": os.getcwd(),
        "output": _output_format(raw_tokens, mcp=invocation.command is None),
        "verbose": "--verbose" in raw_tokens or "-v" in raw_tokens,
    }
    for name in source_fields:
        value = effective_sources.get(name)
        if value is not None:
            resolved[name] = str(value)
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


def _canonical_error_code(code: str) -> str:
    """Keep typed source failures within the established CLI error taxonomy."""
    if code in {"source_not_found", "source_invalid"}:
        return ErrorCode.INPUT_LOAD_FAILED.value
    return code


def _canonical_error_details(invocation: Invocation) -> dict[str, Any]:
    """Retain the caller-selected source spelling in the public CLI envelope."""
    assert invocation.error is not None
    details = _error_details(invocation.error.details)
    request = invocation.request or {}
    if invocation.error.code in {"source_not_found", "source_invalid"}:
        source = details.get("source")
        selected = request.get(source) if isinstance(source, str) else None
        if selected is not None:
            details["path"] = str(selected)
    return details


def _project_actions(
    actions: Any,
    request: Mapping[str, Any] | None,
    *,
    allow_templates: bool,
) -> list[Action]:
    """Project only concrete Agent Surface actions into the v1 action normal form."""
    if actions.truncated:
        raise ValueError("Infralink cannot publish a truncated action frontier")
    projected: list[Action] = []
    for action_value in actions.items:
        template = action_value.command is None
        if template and not allow_templates:
            raise ValueError("Infralink cannot publish an unresolved action template")
        source_command = action_value.command_template if template else action_value.command
        if source_command is None:
            raise ValueError("Infralink action has no command")
        command = _inherit_declared_sources(list(source_command), request)
        _validate_action_operation(action_value.operation, command)
        argv = ["infralink", *_declared_source_argv(request), *command]
        bindings = _project_action_bindings(action_value.slots) if template else {}
        projected.append(
            Action(
                rel=action_value.rel,
                argv=argv,
                command=shlex.join(argv),
                description=action_value.description,
                safe=_action_is_read_only(action_value.operation),
                templated=template,
                bindings=bindings,
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


def _effective_source_values(request: Mapping[str, Any] | None) -> dict[str, Any]:
    """Resolve the selected registry companion paths for public provenance."""
    if request is None:
        return {}
    registry = request.get("registry")
    edges = request.get("edges")
    if registry is None:
        try:
            registry = configured_registry()
        except OperatorConfigError:
            return {"edges": edges} if edges is not None else {}
        if registry is None:
            return {"edges": edges} if edges is not None else {}
    registry_path = Path(str(registry)).expanduser().resolve()
    if edges is None:
        edges = registry_path / "network/main-dev/edges/edges.yml"
    else:
        edges = Path(str(edges)).expanduser().resolve()
    return {"registry": registry_path, "edges": edges}


def _info_action_sources(request: Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    """Keep legacy YAML info actions replayable without inventing an edge path."""
    if request is None or (registry := request.get("registry")) is None:
        return _effective_source_values(request)
    if Path(str(registry)).expanduser().is_dir():
        return _effective_source_values(request)
    return request


def _project_action_bindings(slots: Mapping[str, Any]) -> dict[str, Binding]:
    """Render Agent Surface template slots in Infralink's established binding contract."""
    bindings: dict[str, Binding] = {}
    for name, slot in slots.items():
        if not isinstance(slot, Mapping):
            raise ValueError("Infralink action template slot is invalid")
        slot_type = slot.get("type", "string")
        if slot_type not in {"string", "integer", "boolean"}:
            raise ValueError("Infralink action template slot type is invalid")
        source = slot.get("source")
        if not isinstance(source, str) or not source:
            raise ValueError("Infralink action template slot source is required")
        bindings[name] = Binding(
            type=slot_type,
            required=bool(slot.get("required", False)),
            source=source,
        )
    return bindings


def _validate_action_operation(operation: str | None, argv: list[str]) -> None:
    if operation is None:
        return
    expected = operation.split(".")
    if argv[: len(expected)] != expected and argv != ["help", *expected]:
        raise ValueError("Infralink action command does not match its registered operation")


def _action_is_read_only(operation: str | None) -> bool:
    if operation is None:
        return False
    from agent_surface.operations import UnknownOperationError

    from infralink.operator_surface import app_surface, operator_surface

    for surface in (app_surface, operator_surface):
        try:
            return bool(surface.operations.describe(operation).read_only)
        except UnknownOperationError:
            continue
    return False


__all__ = ["InfralinkEnvelopeRenderer"]
