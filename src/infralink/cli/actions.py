from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from infralink.cli.contracts import Action, Binding

SENSITIVE_OPTIONS = {
    "--access-token",
    "--password",
    "--password-env",
    "--token",
}
_REDACTED = "[REDACTED]"
_SENSITIVE_SHORT_OPTIONS = {"-p"}
_SENSITIVE_ARGV_OPTIONS = SENSITIVE_OPTIONS | _SENSITIVE_SHORT_OPTIONS


def redact_argv(argv: list[str]) -> list[str]:
    """Redact sensitive option values before a command reaches any response."""
    redacted: list[str] = []
    index = 0
    while index < len(argv):
        value = argv[index]
        option, separator, _ = value.partition("=")
        if option in _SENSITIVE_ARGV_OPTIONS:
            redacted.append(f"{option}={_REDACTED}" if separator else option)
            if separator:
                index += 1
                continue
            if (
                index + 1 < len(argv)
                and argv[index + 1].partition("=")[0] not in _SENSITIVE_ARGV_OPTIONS
            ):
                redacted.append(_REDACTED)
                index += 2
                continue
            index += 1
            continue
        attached_short = next(
            (
                short
                for short in _SENSITIVE_SHORT_OPTIONS
                if value.startswith(short)
                and len(value) > len(short)
                and not value.startswith("--")
            ),
            None,
        )
        if attached_short is not None:
            redacted.append(f"{attached_short}{_REDACTED}")
        else:
            redacted.append(value)
        index += 1
    return redacted


def _inherits_resolved_option(option: str, value: str, resolved: Mapping[str, Any]) -> bool:
    """Whether an action can inherit this exact option from its response context."""
    resolved_value = resolved.get(option.removeprefix("--").replace("-", "_"))
    return resolved_value is not None and str(resolved_value) == value


def render_action(
    action_value: Action,
    resolved: Mapping[str, Any],
    *,
    inherited_options: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Render a pasteable action without repeating the current command context."""
    argv: list[str] = []
    index = 0
    while index < len(action_value.argv):
        item = action_value.argv[index]
        if (
            item.startswith("--")
            and "=" not in item
            and index + 1 < len(action_value.argv)
            and item in inherited_options
            and _inherits_resolved_option(item, action_value.argv[index + 1], resolved)
        ):
            index += 2
            continue
        argv.append(item)
        index += 1

    rendered: dict[str, Any] = {
        "rel": action_value.rel,
        "command": shlex.join(redact_argv(argv)),
        "description": action_value.description,
        "safe": action_value.safe,
    }
    if action_value.templated:
        rendered["templated"] = True
        rendered["bindings"] = {
            name: binding.model_dump(mode="json") for name, binding in action_value.bindings.items()
        }
    return rendered


def action(
    rel: str,
    argv: list[str],
    description: str,
    *,
    bindings: dict[str, Binding] | None = None,
    safe: bool = True,
) -> Action:
    active_bindings = {
        name: binding.model_copy(deep=True) for name, binding in (bindings or {}).items()
    }
    copied_argv = list(argv)
    return Action(
        rel=rel,
        argv=copied_argv,
        command=shlex.join(copied_argv),
        description=description,
        safe=safe,
        templated=bool(active_bindings),
        bindings=active_bindings,
    )
