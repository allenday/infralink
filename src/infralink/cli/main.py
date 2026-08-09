"""Main CLI entry point for infralink."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
import yaml

from infralink import __version__
from infralink.cli.actions import action
from infralink.cli.contracts import (
    ArgumentDescriptor,
    Binding,
    CommandContext,
    CommandDescriptor,
    DoctorTarget,
    HelpNavigationAction,
    HelpResult,
    HelpSubcommand,
    HostBootstrapPlanResult,
    InfoResult,
    InfoSources,
    InfoSummary,
    OptionDescriptor,
    RootResult,
    VersionResult,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode, internal_failure
from infralink.cli.host_readiness import evaluate_host_readiness
from infralink.cli.output import (
    command_context,
    error_envelope,
    ok_envelope,
    redact_argv,
)
from infralink.host_transport import SshReadinessTransport

# Topology sources are intentionally explicit. Examples are demo/test fixtures,
# never an implicit operational fallback.
REGISTRY_ENVVAR = "INFRALINK_REGISTRY"
EDGES_ENVVAR = "INFRALINK_EDGES"
_INVOCATION_ARGS: ContextVar[list[str] | None] = ContextVar(
    "infralink_invocation_args", default=None
)
_ENVELOPE_EMITTED: ContextVar[bool] = ContextVar("infralink_envelope_emitted", default=False)
_DEFER_ENVELOPE: ContextVar[bool] = ContextVar("infralink_defer_envelope", default=False)
_PENDING_ENVELOPE: ContextVar[str | None] = ContextVar("infralink_pending_envelope", default=None)
_MANUAL_BOOTSTRAP_ACTIONS = frozenset(
    {"configure_bws", "install_self_deploy_runtime", "enable_self_deploy_timer"}
)


class Context:
    """CLI context object passed to commands."""

    def __init__(self) -> None:
        self.registry_path: Path | None = None
        self.edges_path: Path | None = None
        self.verbose: bool = False
        self.output: str = "yaml"
        self.output_explicit: bool = False
        self._registry: Any = None
        self._edges: Any = None

    @property
    def registry(self) -> Any:
        """Lazy-load registry."""
        if self._registry is None:
            from infralink.core.registry import Registry

            if not self.registry_path:
                raise configuration_required("registry")
            path = str(self.registry_path)
            if not self.registry_path.exists():
                raise input_load_failed("registry", path)
            try:
                if self.registry_path.is_dir():
                    self._registry = Registry.load_dir(self.registry_path)
                else:
                    self._registry = Registry.load(self.registry_path)
            except CliFailure:
                raise
            except Exception:
                raise input_load_failed("registry", path) from None
        return self._registry

    @property
    def edges(self) -> Any:
        """Lazy-load edges."""
        if self._edges is None:
            from infralink.core.edges import EdgeSet

            if self.edges_path is not None:
                path = str(self.edges_path)
                if not self.edges_path.exists():
                    raise input_load_failed("edges", path)
                try:
                    self._edges = EdgeSet.load(self.edges_path)
                except Exception:
                    raise input_load_failed("edges", path) from None
            else:
                # Try loading from registry
                import yaml

                from infralink.core.edges import EdgeSet

                if self.registry_path and self.registry_path.exists():
                    if self.registry_path.is_dir():
                        raise configuration_required("edges")
                    else:
                        try:
                            with self.registry_path.open() as f:
                                data = yaml.safe_load(f)
                            self._edges = EdgeSet.from_registry(data)
                        except Exception:
                            raise input_load_failed("registry", str(self.registry_path)) from None
                else:
                    raise configuration_required("edges")
        return self._edges


pass_context = click.make_pass_decorator(Context, ensure=True)


COMMAND_METADATA: dict[str, dict[str, Any]] = {
    "help": {
        "description": "Show machine-readable command help.",
        "usage": "infralink help [command ...]",
    },
    "version": {
        "description": "Show CLI and schema versions.",
        "usage": "infralink version",
    },
    "analyze": {
        "description": "Analyze registry and generate derived artifacts.",
        "usage": "infralink analyze --output <directory>",
    },
    "check": {"description": "Run health checks for edges.", "usage": "infralink check"},
    "doctor": {
        "description": "Validate declared observation coverage and inspect declared evidence.",
        "usage": "infralink doctor [host|service|edge|profile <ref>] [--validate]",
    },
    "diagram": {
        "description": "Generate topology diagrams.",
        "usage": "infralink diagram --output <directory>",
    },
    "docs": {
        "description": "Generate documentation outputs.",
        "usage": "infralink docs --output <directory>",
    },
    "resolve": {
        "description": "Resolve an edge to targets.",
        "usage": "infralink resolve <edge-id>",
    },
    "validate": {
        "description": "Validate registry and edges.",
        "usage": "infralink validate",
    },
    "app": {"description": "Manage applications.", "usage": "infralink app [list|show]"},
    "info": {"description": "Show registry and edge summary.", "usage": "infralink info"},
    "host": {
        "description": "Inspect, scaffold, or plan bootstrap for hosts.",
        "usage": "infralink host [create|list|show|bootstrap]",
    },
    "edge": {"description": "Inspect edges.", "usage": "infralink edge show <edge-id>"},
    "service": {
        "description": "Inspect services.",
        "usage": "infralink service show <service-id>",
    },
    "secrets": {
        "description": "Inspect and audit secret references.",
        "usage": "infralink secrets [inspect|audit]",
    },
    "capabilities": {
        "description": "Describe offline observation capabilities.",
        "usage": "infralink capabilities",
    },
    "project": {
        "description": "Project observation contracts.",
        "usage": "infralink project [observation|secrets|view|readiness]",
    },
    "explain": {
        "description": "Explain an observation diagnostic code.",
        "usage": "infralink explain ERROR_CODE",
    },
    "release": {
        "description": "Inspect validated immutable registry releases.",
        "usage": "infralink release inspect --release-validation PATH --admission PATH",
    },
}


HELP_METADATA: dict[tuple[str, ...], dict[str, Any]] = {
    (): {
        "description": "Infrastructure topology modeling.",
        "arguments": [],
        "options": [
            {"name": "registry", "type": "path", "required": False},
            {"name": "edges", "type": "path", "required": False},
            {"name": "verbose", "type": "boolean", "required": False},
            {"name": "output", "type": "choice", "required": False},
        ],
        "examples": ["infralink", "infralink help resolve"],
    },
    ("resolve",): {
        "description": "Resolve an edge to its target endpoint.",
        "arguments": [{"name": "edge_id", "type": "string", "required": True}],
        "options": [
            {"name": "user", "type": "string", "required": False},
            {"name": "database", "type": "string", "required": False},
            {"name": "prefer_ip", "type": "choice", "required": False},
        ],
        "examples": ["infralink resolve 058e29ff-57b9-47c8-b6fa-0914ac03e25c"],
    },
    ("app", "list"): {
        "description": "List application groupings.",
        "arguments": [],
        "options": [
            {"name": "limit", "type": "integer", "required": False},
            {"name": "cursor", "type": "string", "required": False},
            {"name": "collection", "type": "string", "required": False},
        ],
        "examples": ["infralink app list"],
    },
    ("app", "show"): {
        "description": "Show one application grouping.",
        "arguments": [{"name": "app_id", "type": "string", "required": True}],
        "options": [
            {"name": "limit", "type": "integer", "required": False},
            {"name": "cursor", "type": "string", "required": False},
            {"name": "collection", "type": "string", "required": False},
        ],
        "examples": ["infralink app show core"],
    },
    ("host",): {
        "description": "Inspect hosts.",
        "arguments": [],
        "options": [],
        "examples": ["infralink host show host-1"],
    },
    ("host", "show"): {
        "description": "Show one host.",
        "arguments": [{"name": "host_id", "type": "string", "required": True}],
        "options": [
            {"name": "limit", "type": "integer", "required": False},
            {"name": "cursor", "type": "string", "required": False},
            {"name": "collection", "type": "string", "required": False},
        ],
        "examples": ["infralink host show host-1"],
    },
    ("host", "bootstrap"): {
        "description": "Plan host bootstrap actions without applying them.",
        "arguments": [{"name": "host_id", "type": "string", "required": True}],
        "options": [
            {"name": "plan", "type": "boolean", "required": False},
            {"name": "apply", "type": "boolean", "required": False},
        ],
        "examples": ["infralink host bootstrap host-1 --plan", "infralink host bootstrap host-1 --apply"],
    },
    ("edge",): {
        "description": "Inspect edges.",
        "arguments": [],
        "options": [],
        "examples": ["infralink edge show edge-1"],
    },
    ("edge", "show"): {
        "description": "Show one edge.",
        "arguments": [{"name": "edge_id", "type": "string", "required": True}],
        "options": [
            {"name": "limit", "type": "integer", "required": False},
            {"name": "cursor", "type": "string", "required": False},
            {"name": "collection", "type": "string", "required": False},
        ],
        "examples": ["infralink edge show edge-1"],
    },
    ("service",): {
        "description": "Inspect services.",
        "arguments": [],
        "options": [],
        "examples": ["infralink service show api"],
    },
    ("service", "show"): {
        "description": "Show one service.",
        "arguments": [{"name": "service_id", "type": "string", "required": True}],
        "options": [
            {"name": "limit", "type": "integer", "required": False},
            {"name": "cursor", "type": "string", "required": False},
            {"name": "collection", "type": "string", "required": False},
        ],
        "examples": ["infralink service show api"],
    },
    ("secrets",): {
        "description": "Inspect declared secret references or audit provider metadata.",
        "arguments": [],
        "options": [],
        "examples": ["infralink secrets inspect"],
    },
    ("secrets", "inspect"): {
        "description": "Inspect declared secret-reference metadata without provider access.",
        "arguments": [],
        "options": [],
        "examples": ["infralink secrets inspect"],
    },
    ("secrets", "audit"): {
        "description": "Audit declared secret-reference metadata with a provider.",
        "arguments": [],
        "options": [],
        "examples": ["infralink secrets audit"],
    },
    ("release",): {
        "description": "Inspect validated immutable registry releases.",
        "arguments": [],
        "options": [],
        "examples": [
            "infralink release inspect --release-validation release-validation.json --admission release-admission.yml"
        ],
    },
    ("release", "inspect"): {
        "description": "Inspect a validated release against bounded local admission policy.",
        "arguments": [],
        "options": [
            {"name": "release_validation", "type": "path", "required": True},
            {"name": "admission", "type": "path", "required": True},
        ],
        "examples": [
            "infralink release inspect --release-validation release-validation.json --admission release-admission.yml"
        ],
    },
    ("release", "validate-candidate"): {
        "description": "Validate a local immutable release candidate without publishing it.",
        "arguments": [],
        "options": [{"name": "candidate", "type": "path", "required": True}],
        "examples": ["infralink release validate-candidate --candidate candidate.json"],
    },
    ("release", "render-publisher-request"): {
        "description": "Inspect a registry-rendered immutable publisher request without invoking it.",
        "arguments": [],
        "options": [
            {"name": "publisher_request", "type": "path", "required": False},
            {"name": "candidate", "type": "path", "required": False},
            {"name": "admission", "type": "path", "required": False},
        ],
        "examples": [
            "infralink release render-publisher-request --publisher-request publisher-request.v2.json",
            "infralink release render-publisher-request --candidate candidate.json --admission admission.yml",
        ],
    },
    ("release", "inspect-attestation"): {
        "description": "Inspect a publisher completion record without contacting a provider.",
        "arguments": [],
        "options": [{"name": "attestation", "type": "path", "required": True}],
        "examples": ["infralink release inspect-attestation --attestation attestation.json"],
    },
}


def _context_for(
    argv: list[str] | None = None,
    path: list[str] | None = None,
) -> CommandContext:
    active_argv = argv
    if active_argv is None:
        active_argv = _INVOCATION_ARGS.get() or []
    redacted_argv = redact_argv(active_argv)
    parsed_path, parsed_args, root_values = _parse_invocation(redacted_argv)
    resolved = {
        "version": __version__,
        "cwd": os.getcwd(),
        "registry": _source_value(root_values.get("registry")),
        "edges": _source_value(root_values.get("edges")),
        "output": root_values.get("output", "yaml"),
        "verbose": bool(root_values.get("verbose", False)),
    }
    resolved.update(_command_resolved_overrides(redacted_argv, parsed_path))
    if parsed_path and parsed_path[0] == "doctor":
        gatus_url = os.environ.get("INFRALINK_GATUS_URL")
        for index, item in enumerate(redacted_argv):
            if item.startswith("--gatus-url="):
                gatus_url = item.split("=", 1)[1]
            elif item == "--gatus-url" and index + 1 < len(redacted_argv):
                gatus_url = redacted_argv[index + 1]
        resolved["gatus_configured"] = bool(gatus_url)
        if gatus_url:
            resolved["gatus_url"] = gatus_url
    return command_context(
        ["infralink", *redacted_argv],
        path=path if path is not None else parsed_path,
        args=parsed_args,
        flags=[item for item in redacted_argv if item.startswith("-")],
        resolved=resolved,
    )


def _command_resolved_overrides(
    argv: list[str],
    parsed_path: list[str],
) -> dict[str, Any]:
    """Derive command-local effective sources from the same Click parser."""
    if parsed_path != ["analyze"]:
        return {}
    root_ctx = cli.make_context("infralink", list(argv), resilient_parsing=True)
    if _protected_args(root_ctx) != ["analyze"]:
        return {}
    command = _load_command("analyze")
    if command is None:
        return {}
    command_ctx = command.make_context(
        "infralink analyze",
        list(root_ctx.args),
        resilient_parsing=True,
    )
    registry_override = command_ctx.params.get("registry_override")
    if registry_override is None:
        return {}
    return {"registry": str(registry_override)}


def input_load_failed(source: str, path: str) -> CliFailure:
    return CliFailure(
        code=ErrorCode.INPUT_LOAD_FAILED,
        message=f"{source.title()} could not be loaded",
        exit_code=3,
        fix=f"Provide a valid {source} input",
        details={"source": source, "path": path},
        next_actions=[
            action(
                "help",
                [*_action_argv_prefix(), "help", "validate"],
                "Show validation input options",
            )
        ],
    )


def configuration_required(source: str) -> CliFailure:
    return CliFailure(
        code=ErrorCode.CONFIGURATION_REQUIRED,
        message=f"{source.title()} configuration is required",
        exit_code=ExitCode.USAGE_ERROR,
        fix=f"Set {REGISTRY_ENVVAR if source == 'registry' else EDGES_ENVVAR} or provide --{source}",
        details={"source": source},
        next_actions=[
            action(
                "help",
                [
                    *_action_argv_prefix(),
                    "help",
                    *(["host", "list"] if source == "registry" else ["edge", "list"]),
                ],
                "Show topology input options",
            )
        ],
    )


def _source_value(path: Path | None) -> str | None:
    return str(path) if path is not None else None


def _action_argv_prefix() -> list[str]:
    """Return an executable prefix that preserves an explicit output selection."""
    if _output_from_argv(_INVOCATION_ARGS.get() or []) == "json":
        return ["infralink", "--output", "json"]
    return ["infralink"]


def _protected_args(ctx: click.Context) -> list[str]:
    return list(getattr(ctx, "_protected_args", []))


def _parse_invocation(
    argv: list[str],
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    root_ctx = cli.make_context("infralink", list(argv), resilient_parsing=True)
    root_values = dict(root_ctx.params)
    protected = _protected_args(root_ctx)
    if not protected:
        return [], {}, root_values

    name = protected[0]
    path = [name]
    command = _load_command(name)
    remaining = list(root_ctx.args)
    if command is None:
        candidate = tuple([name, *[item for item in remaining if not item.startswith("-")]][:2])
        if candidate in HELP_METADATA:
            path = list(candidate)
        return path, {}, root_values

    current = command
    while isinstance(current, click.Group):
        command_ctx = current.make_context(
            f"infralink {' '.join(path)}",
            list(remaining),
            resilient_parsing=True,
        )
        nested = _protected_args(command_ctx)
        if not nested:
            return path, {}, root_values
        child_name = nested[0]
        child = current.get_command(command_ctx, child_name)
        if child is None:
            return path, {}, root_values
        path.append(child_name)
        current = child
        remaining = list(command_ctx.args)

    command_ctx = current.make_context(
        f"infralink {' '.join(path)}",
        list(remaining),
        resilient_parsing=True,
    )
    positional_names = {
        parameter.name
        for parameter in current.params
        if isinstance(parameter, click.Argument) and parameter.name
    }
    parsed_args = {
        name: value
        for name, value in command_ctx.params.items()
        if name in positional_names and value is not None
    }
    if not parsed_args:
        positional_values = _declared_positionals(current, remaining)
        argument_parameters = [
            parameter
            for parameter in current.params
            if isinstance(parameter, click.Argument) and parameter.name
        ]
        parsed_args = {
            parameter.name: value
            for parameter, value in zip(argument_parameters, positional_values, strict=False)
        }
    return path, parsed_args, root_values


def _declared_positionals(command: click.Command, argv: list[str]) -> list[str]:
    options = {
        option: parameter
        for parameter in command.params
        if isinstance(parameter, click.Option)
        for option in parameter.opts
    }
    values: list[str] = []
    index = 0
    while index < len(argv):
        token = argv[index]
        option_name = token.partition("=")[0]
        parameter = options.get(option_name)
        if parameter is None and token.startswith("-") and not token.startswith("--"):
            parameter = next(
                (
                    candidate
                    for option, candidate in options.items()
                    if token.startswith(option) and len(token) > len(option)
                ),
                None,
            )
        if parameter is not None:
            if (
                not parameter.is_flag
                and "=" not in token
                and not (
                    token.startswith("-")
                    and not token.startswith("--")
                    and any(
                        token.startswith(option) and len(token) > len(option)
                        for option in parameter.opts
                    )
                )
            ):
                index += parameter.nargs
        elif not token.startswith("-"):
            values.append(token)
        index += 1
    return values


def _normalize_discovery_aliases(argv: list[str]) -> list[str]:
    if "--version" in argv:
        index = argv.index("--version")
        if index == len(argv) - 1:
            return [*argv[:index], "version"]
    if "--help" not in argv:
        return argv

    path, _, _ = _parse_invocation(redact_argv(argv[: argv.index("--help")]))
    return ["--output", _output_from_argv(argv), "help", *path]


def _emit(payload: dict[str, Any]) -> None:
    serialized = _serialize(payload)
    _ENVELOPE_EMITTED.set(True)
    if _DEFER_ENVELOPE.get():
        _PENDING_ENVELOPE.set(serialized)
    else:
        click.echo(serialized)


def _serialize(payload: dict[str, Any]) -> str:
    """Serialize envelopes in the root invocation's selected output format."""
    context = click.get_current_context(silent=True)
    if context is not None:
        root = context.find_root()
        if isinstance(root.obj, Context):
            output = root.obj.output
        else:
            output = _output_from_argv(_INVOCATION_ARGS.get() or [])
    else:
        incoming = _INVOCATION_ARGS.get()
        # JsonGroup is reusable outside the public CLI. Preserve its historical
        # JSON fallback when no Infralink root invocation established a format.
        output = "json" if incoming is None else _output_from_argv(incoming)
    if output == "json":
        return json.dumps(payload, separators=(",", ":"))
    return yaml.safe_dump(payload, sort_keys=False).rstrip("\n")


def _output_from_argv(argv: list[str]) -> str:
    """Read the root output option without re-entering Click command parsing."""
    output = "yaml"
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--" or not token.startswith("-"):
            break
        if token in {"--registry", "--edges", "-r", "-e"}:
            index += 2
            continue
        if token.startswith(("--registry=", "--edges=")):
            index += 1
            continue
        if token in {"--output", "-o"}:
            if index + 1 < len(argv):
                output = argv[index + 1].lower()
            index += 2
            continue
        if token.startswith("--output="):
            output = token.partition("=")[2].lower()
            index += 1
            continue
        if token.startswith("-") and not token.startswith("--"):
            cluster = token[1:]
            output_offset = cluster.find("o")
            if output_offset >= 0:
                attached = cluster[output_offset + 1 :]
                if attached:
                    output = attached.lower()
                    index += 1
                elif index + 1 < len(argv):
                    output = argv[index + 1].lower()
                    index += 2
                else:
                    index += 1
                continue
        index += 1
    return output if output in {"json", "yaml"} else "yaml"


def _help_result(path: tuple[str, ...]) -> HelpResult:
    command = _command_for_path(path)
    if command is None:
        raise click.UsageError("Unknown command path")
    arguments, options = _help_parameters(command)
    return HelpResult(
        path=list(path),
        description=_command_description(command),
        arguments=arguments,
        options=options,
        examples=list(HELP_METADATA.get(path, {}).get("examples", [])),
        children=_help_children(path, command),
    )


def _help_parameters(
    command: click.Command,
) -> tuple[list[ArgumentDescriptor], list[OptionDescriptor]]:
    arguments: list[ArgumentDescriptor] = []
    options: list[OptionDescriptor] = []
    for parameter in command.params:
        if isinstance(parameter, click.Argument):
            arguments.append(
                ArgumentDescriptor(
                    name=parameter.name or "",
                    type=parameter.type.name,
                    required=parameter.required,
                )
            )
        elif isinstance(parameter, click.Option):
            long_option = next(
                (option for option in parameter.opts if option.startswith("--")),
                parameter.name or "",
            )
            options.append(
                OptionDescriptor(
                    name=long_option.removeprefix("--").replace("-", "_"),
                    type=parameter.type.name,
                    required=parameter.required,
                )
            )
    return arguments, options


def _command_description(command: click.Command) -> str:
    callback = command.callback
    return (
        command.help
        or command.short_help
        or (callback.__doc__ if callback is not None else "")
        or ""
    )


def _help_children(path: tuple[str, ...], command: click.Command) -> list[HelpSubcommand]:
    if not isinstance(command, click.Group):
        return []
    context = click.Context(command)
    children: list[HelpSubcommand] = []
    for name in command.list_commands(context):
        child = command.get_command(context, name)
        if child is None:
            continue
        argv = [*_help_argv_prefix(), *path, name]
        action_value = HelpNavigationAction(command=" ".join(argv), argv=argv)
        children.append(
            HelpSubcommand(
                name=name,
                summary=_command_summary(child),
                action=action_value,
            )
        )
    return children


def _command_summary(command: click.Command) -> str:
    return next(
        (line.strip() for line in _command_description(command).splitlines() if line.strip()),
        "",
    )


def _help_argv_prefix() -> list[str]:
    context = click.get_current_context(silent=True)
    if context is not None:
        root = context.find_root()
        if isinstance(root.obj, Context) and root.obj.output_explicit:
            return ["infralink", "--output", root.obj.output, "help"]
    if _output_from_argv(_INVOCATION_ARGS.get() or []) == "json":
        return ["infralink", "--output", "json", "help"]
    return ["infralink", "help"]


def _command_for_path(path: tuple[str, ...]) -> click.Command | None:
    if not path:
        return cli
    command = _load_command(path[0])
    if command is None:
        return None
    for name in path[1:]:
        if not isinstance(command, click.Group):
            return None
        command = command.get_command(click.Context(command), name)
        if command is None:
            return None
    return command


def _emit_help(path: tuple[str, ...], argv: list[str] | None = None) -> None:
    result = _help_result(path)
    _emit(
        ok_envelope(
            _context_for(argv, list(path)),
            result,
            [],
        )
    )


def _usage_actions(path: list[str], artifact_command: str | None) -> list:
    if artifact_command is not None:
        return [
            action(
                "help",
                [*_help_argv_prefix(), artifact_command],
                "Show command usage",
            )
        ]
    canonical_alias = {
        ("hosts",): ("host", "list"),
        ("services",): ("service", "list"),
        ("edges-list",): ("edge", "list"),
    }.get(tuple(path))
    if canonical_alias is not None:
        return [
            action(
                "help",
                [*_help_argv_prefix(), *canonical_alias],
                "Show canonical command help",
            )
        ]
    command = _command_for_path(tuple(path))
    if isinstance(command, click.Group):
        return [
            action(
                f"help-{child.name}",
                child.action.argv,
                f"Show {child.name} command help",
            )
            for child in _help_children(tuple(path), command)
        ]
    return [action("help", _help_argv_prefix(), "Show command usage")]


def entity_not_found(entity_type: str, requested_id: str) -> CliFailure:
    """Compatibility import for commands implemented before query extraction."""
    from infralink.cli.queries import entity_not_found as query_entity_not_found

    return query_entity_not_found(entity_type, requested_id)


def _load_command(name: str) -> click.Command | None:
    if name == "help":
        return help_command
    if name == "version":
        return version_command
    if name == "analyze":
        from infralink.cli.analyze import analyze

        return analyze
    if name == "check":
        from infralink.cli.check import check

        return check
    if name == "doctor":
        from infralink.cli.doctor import doctor

        return doctor
    if name == "diagram":
        from infralink.cli.diagram import diagram

        return diagram
    if name == "docs":
        from infralink.cli.docs import docs

        return docs
    if name == "resolve":
        from infralink.cli.resolve import resolve

        return resolve
    if name == "validate":
        from infralink.cli.validate import validate

        return validate
    if name == "capabilities":
        from infralink.cli.observation import capabilities

        return capabilities
    if name == "project":
        from infralink.cli.observation import project_group

        return project_group
    if name == "explain":
        from infralink.cli.observation import explain_command

        return explain_command
    if name == "app":
        from infralink.cli.app import app

        return app
    if name == "info":
        return info
    if name == "hosts":
        return hosts
    if name == "edges-list":
        return edges_list
    if name == "services":
        return services
    if name == "host":
        return host
    if name == "edge":
        return edge
    if name == "service":
        return service
    if name == "secrets":
        from infralink.cli.secrets import secrets

        return secrets
    if name == "release":
        from infralink.cli.release import release

        return release
    return None


class JsonGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(name for name in COMMAND_METADATA if _load_command(name) is not None)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        return _load_command(cmd_name)

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        **extra: Any,
    ) -> Any:
        incoming = list(sys.argv[1:] if args is None else args)
        normalized = _normalize_discovery_aliases(incoming)
        invocation_token = _INVOCATION_ARGS.set(incoming)
        emitted_token = _ENVELOPE_EMITTED.set(False)
        deferred_token = _DEFER_ENVELOPE.set(True)
        pending_token = _PENDING_ENVELOPE.set(None)
        exit_code: int = ExitCode.POSITIVE_RESULT
        pending_envelope: str | None = None
        try:
            try:
                result = super().main(
                    args=normalized,
                    prog_name=prog_name,
                    complete_var=complete_var,
                    standalone_mode=False,
                    **extra,
                )
                if isinstance(result, int):
                    try:
                        exit_code = ExitCode(result)
                    except ValueError:
                        failure = internal_failure()
                        _emit(error_envelope(_context_for(incoming), failure))
                        exit_code = failure.exit_code
            except click.UsageError:
                path, _, _ = _parse_invocation(redact_argv(incoming))
                from infralink.cli.observation import is_observation_argv

                observation_command = is_observation_argv(incoming)
                if observation_command:
                    from infralink.cli.observation import emit_boundary_failure

                    emit_boundary_failure(
                        incoming, code="invocation-error", message="Invalid command usage"
                    )
                    exit_code = ExitCode.USAGE_ERROR
                    continue_after_usage = True
                else:
                    continue_after_usage = False
                artifact_command = (
                    path[0] if path and path[0] in {"analyze", "diagram", "docs"} else None
                )
                usage_failure = CliFailure(
                    code=ErrorCode.USAGE_ERROR,
                    message="Invalid command usage",
                    exit_code=ExitCode.USAGE_ERROR,
                    fix=(
                        "Provide an explicit safe relative --output directory"
                        if artifact_command is not None
                        else "Run infralink help"
                    ),
                    next_actions=_usage_actions(path, artifact_command),
                )
                if not continue_after_usage and not _ENVELOPE_EMITTED.get():
                    _emit(error_envelope(_context_for(incoming), usage_failure))
                if not continue_after_usage:
                    exit_code = usage_failure.exit_code
            except CliFailure as cli_failure:
                if not _ENVELOPE_EMITTED.get():
                    _emit(error_envelope(_context_for(incoming), cli_failure))
                exit_code = cli_failure.exit_code
            except SystemExit as system_exit:
                if _ENVELOPE_EMITTED.get() and isinstance(system_exit.code, int):
                    try:
                        exit_code = ExitCode(system_exit.code)
                    except ValueError:
                        failure = internal_failure()
                        _emit(error_envelope(_context_for(incoming), failure))
                        exit_code = failure.exit_code
                else:
                    failure = internal_failure()
                    _emit(error_envelope(_context_for(incoming), failure))
                    exit_code = failure.exit_code
            except Exception:
                from infralink.cli.observation import is_observation_argv

                observation_command = is_observation_argv(incoming)
                if observation_command:
                    from infralink.cli.observation import emit_boundary_failure

                    emit_boundary_failure(
                        incoming,
                        code="internal-invariant",
                        message="An internal invariant failed",
                    )
                    exit_code = 4
                else:
                    failure = internal_failure()
                    _emit(error_envelope(_context_for(incoming), failure))
                    exit_code = failure.exit_code
        finally:
            pending_envelope = _PENDING_ENVELOPE.get()
            _PENDING_ENVELOPE.reset(pending_token)
            _DEFER_ENVELOPE.reset(deferred_token)
            _ENVELOPE_EMITTED.reset(emitted_token)
            _INVOCATION_ARGS.reset(invocation_token)
        if pending_envelope is not None:
            click.echo(pending_envelope)
        if standalone_mode:
            raise SystemExit(exit_code)
        return exit_code


LazyGroup = JsonGroup


@click.command(name="help")
@click.argument("path", nargs=-1)
def help_command(path: tuple[str, ...]) -> None:
    """Show machine-readable command help."""
    _emit_help(path)


@click.command(name="version")
def version_command() -> None:
    """Show CLI and schema versions."""
    _emit(
        ok_envelope(
            _context_for(path=["version"]),
            VersionResult(version=__version__, cli_schema_version="infralink.cli/v1"),
            [],
        )
    )


@click.group(
    cls=JsonGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": []},
)
@click.option(
    "-r",
    "--registry",
    type=click.Path(exists=False, path_type=Path),
    default=None,
    envvar=REGISTRY_ENVVAR,
    help="Path to registry YAML file",
)
@click.option(
    "-e",
    "--edges",
    type=click.Path(exists=False, path_type=Path),
    default=None,
    envvar=EDGES_ENVVAR,
    help="Path to edges YAML file",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@click.option(
    "-o",
    "--output",
    type=click.Choice(["json", "yaml"], case_sensitive=False),
    default="yaml",
    show_default=True,
    help="Output format (yaml by default; json for explicit machine parsing)",
)
@pass_context
def cli(
    ctx: Context, registry: Path | None, edges: Path | None, verbose: bool, output: str
) -> None:
    """
    Infralink - Infrastructure topology modeling.

    Manage infrastructure nodes and edges for health checks,
    diagram generation, and documentation.
    """
    ctx.registry_path = registry
    ctx.edges_path = edges
    ctx.verbose = verbose
    ctx.output = output
    ctx.output_explicit = (
        click.get_current_context().get_parameter_source("output")
        is not click.core.ParameterSource.DEFAULT
    )

    click_ctx = click.get_current_context()
    if click_ctx.invoked_subcommand is not None:
        return

    live_commands = cli.list_commands(click_ctx)
    command_tree = RootResult(
        version=__version__,
        commands=[
            CommandDescriptor(
                name=name,
                description=meta.get("description", ""),
                usage=meta.get("usage", f"infralink {name}"),
            )
            for name in live_commands
            if (meta := COMMAND_METADATA.get(name)) is not None
        ],
    )

    payload = ok_envelope(
        _context_for(path=[]),
        command_tree,
        [
            action("help", _help_argv_prefix(), "Show available commands"),
            action("list", [*_root_source_argv(ctx), "service", "list"], "List declared services"),
            action("version", [*_root_action_prefix(ctx), "version"], "Show CLI version"),
        ],
    )
    _emit(payload)


def _topology_fingerprint(
    ctx: Context,
    *,
    include_registry: bool,
    include_edges: bool,
    identifiers: dict[str, str] | None = None,
) -> str:
    snapshot: dict[str, Any] = {
        "registry_path": str(ctx.registry_path),
        "edges_path": str(ctx.edges_path),
        "identifiers": identifiers or {},
    }
    if include_registry:
        snapshot["hosts"] = [
            host.to_dict() for host in sorted(ctx.registry, key=lambda item: item.uuid)
        ]
        snapshot["applications"] = [
            application.to_dict()
            for application in sorted(ctx.registry.applications, key=lambda item: item.id)
        ]
    if include_edges:
        snapshot["edges"] = [edge.to_dict() for edge in sorted(ctx.edges, key=lambda item: item.id)]
    serialized = json.dumps(
        snapshot,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def _page_offset(
    *,
    command: str,
    collection: str,
    cursor: str | None,
    fingerprint: str,
) -> int:
    if cursor is None:
        return 0
    from infralink.cli.pagination import production_cursor_codec

    return production_cursor_codec().decode(cursor, command, collection, fingerprint)


def _active_collection(
    collection: str | None,
    cursor: str | None,
    allowed: tuple[str, ...],
) -> str:
    if cursor is not None and len(allowed) > 1 and collection is None:
        from infralink.cli.pagination import invalid_cursor

        raise invalid_cursor()
    selected = collection or allowed[0]
    if selected not in allowed:
        from infralink.cli.pagination import invalid_cursor

        raise invalid_cursor()
    return selected


def _attach_next_cursors(
    result: Any,
    *,
    command: str,
    collections: tuple[str, ...],
    selected: str,
    offset: int,
    limit: int,
    fingerprint: str,
) -> None:
    from infralink.cli.pagination import production_cursor_codec

    codec = None
    for collection in collections:
        page = result.page if collection == "items" else getattr(result, collection).page
        collection_offset = offset if collection == selected else 0
        if page.total is None or collection_offset + page.returned >= page.total:
            continue
        if codec is None:
            codec = production_cursor_codec()
        page.next_cursor = codec.encode(
            command,
            collection,
            collection_offset + limit,
            fingerprint,
        )


def _root_source_argv(ctx: Context) -> list[str]:
    argv = _root_action_prefix(ctx)
    if ctx.registry_path is not None:
        argv.extend(["--registry", str(ctx.registry_path)])
    if ctx.edges_path is not None:
        argv.extend(["--edges", str(ctx.edges_path)])
    return argv


def _root_action_prefix(ctx: Context) -> list[str]:
    argv = ["infralink"]
    if ctx.output_explicit:
        argv.extend(["--output", ctx.output])
    return argv


def _compatibility_action(ctx: Context, path: list[str], canonical: list[str]) -> list:
    if path == canonical:
        return []
    return [
        action(
            "canonical",
            [*_root_action_prefix(ctx), *canonical],
            f"Use canonical {' '.join(canonical)} command",
        )
    ]


def _summary_detail_actions(
    ctx: Context,
    result: Any,
    path: list[str],
    command_argv: list[str],
) -> list[Any]:
    if path in (
        ["hosts"],
        ["services"],
        ["edges-list"],
        ["host", "list"],
        ["service", "list"],
        ["edge", "list"],
    ):
        entity = {
            "hosts": "host",
            "services": "service",
            "edges-list": "edge",
            "host list": "host",
            "service list": "service",
            "edge list": "edge",
        }[" ".join(path)]
    elif path == ["app", "list"]:
        entity = "app"
    else:
        return []
    return [
        action(
            "show",
            [*_root_action_prefix(ctx), entity, "show", "{id}"],
            f"Show one {entity}",
            bindings={
                "id": Binding(
                    type="string",
                    required=True,
                    source="result.items[]",
                )
            },
        )
    ]


def _emit_query_result(
    *,
    ctx: Context,
    path: list[str],
    command_argv: list[str],
    result: Any,
    limit: int | None = None,
    extra_actions: list[Any] | None = None,
    resolved: dict[str, Any] | None = None,
    content_truncated: bool = False,
) -> None:
    pages: list[tuple[str, Any, str]] = []
    if hasattr(result, "page"):
        pages.append(("items", result.page, "result.page.next_cursor"))
    else:
        for name in (
            "services",
            "projects",
            "hosts",
            "ports",
            "protocols",
            "secret_refs",
            "edges",
            "errors",
            "warnings",
            "checks",
            "references",
            "locations",
        ):
            page = getattr(result, name, None)
            if page is not None:
                pages.append((name, page.page, f"result.{name}.page.next_cursor"))
    invocation_argv = [*_root_action_prefix(ctx), *command_argv]
    actions = [
        action(
            "help",
            [*_help_argv_prefix(), *path],
            f"Show {' '.join(path)} help",
        )
    ]
    actions.extend(_summary_detail_actions(ctx, result, path, command_argv))
    actions.extend(extra_actions or [])
    for collection, page, source in pages:
        if page.next_cursor is None or limit is None:
            continue
        actions.append(
            action(
                "continue",
                [
                    *invocation_argv,
                    "--collection",
                    collection,
                    "--cursor",
                    "{cursor}",
                    "--limit",
                    str(limit),
                ],
                f"Continue {collection}",
                bindings={
                    "cursor": Binding(
                        type="string",
                        required=True,
                        source=source,
                    )
                },
            )
        )
    command_context = _context_for(path=path)
    command_context.resolved.update(resolved or {})
    payload = ok_envelope(command_context, result, actions)
    payload["meta"]["truncated"] = content_truncated or any(
        page.next_cursor is not None for _, page, _ in pages
    )
    _emit(payload)


def _page_options(command: Any) -> Any:
    command = click.option(
        "--collection",
        type=str,
        default=None,
        help="Paged result field to continue",
    )(command)
    command = click.option("--cursor", type=str, default=None)(command)
    return click.option(
        "--limit",
        type=click.IntRange(1, 1000),
        default=20,
        show_default=True,
    )(command)


@click.command()
@pass_context
def services(ctx: Context) -> None:
    """List services declared by registry hosts."""
    _emit_service_list(ctx, path=["services"])


def _emit_service_list(ctx: Context, *, path: list[str]) -> None:
    from infralink.cli.queries import list_services

    result = list_services(ctx.registry, ctx.edges)
    _emit_query_result(
        ctx=ctx,
        path=path,
        command_argv=path,
        result=result,
        extra_actions=_compatibility_action(ctx, path, ["service", "list"]),
    )


@cli.command()
@pass_context
def info(ctx: Context) -> None:
    """Show registry and edge summary."""
    from infralink.cli.queries import list_services

    registry = ctx.registry
    edges = ctx.edges
    service_count = len(list_services(registry, edges).items)
    result = InfoResult(
        sources=InfoSources(
            registry=str(ctx.registry_path),
            edges=str(ctx.edges_path),
        ),
        summary=InfoSummary(
            host_count=len(registry),
            service_count=service_count or 0,
            edge_count=len(edges),
        ),
    )
    payload = ok_envelope(
        _context_for(path=["info"]),
        result,
        [
            action("list", [*_root_source_argv(ctx), "host", "list"], "List all hosts"),
            action("list", [*_root_source_argv(ctx), "edge", "list"], "List all edges"),
        ],
    )
    _emit(payload)


@click.command()
@pass_context
def hosts(ctx: Context) -> None:
    """List all hosts in registry."""
    _emit_host_list(ctx, path=["hosts"])


def _emit_host_list(
    ctx: Context,
    *,
    path: list[str],
) -> None:
    from infralink.cli.queries import list_hosts

    result = list_hosts(ctx.registry)
    _emit_query_result(
        ctx=ctx,
        path=path,
        command_argv=path,
        result=result,
        extra_actions=_compatibility_action(ctx, path, ["host", "list"]),
    )


@click.command(name="edges-list")
@pass_context
def edges_list(ctx: Context) -> None:
    """List all declared edges."""
    _emit_edge_list(ctx, path=["edges-list"])


def _emit_edge_list(ctx: Context, *, path: list[str]) -> None:
    from infralink.cli.queries import list_edges

    result = list_edges(ctx.edges)
    _emit_query_result(
        ctx=ctx,
        path=path,
        command_argv=path,
        result=result,
        extra_actions=_compatibility_action(ctx, path, ["edge", "list"]),
    )


@click.group()
def host() -> None:
    """Inspect or scaffold hosts."""


_HOSTNAME_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)


def _host_address(value: str) -> tuple[str, str]:
    try:
        return "tailscale_ip", str(ipaddress.ip_address(value))
    except ValueError:
        if _HOSTNAME_PATTERN.fullmatch(value):
            return "tailscale_name", value.lower()
    raise click.BadParameter("must be an IP address or DNS hostname")


def _host_create_failure(message: str, fix: str, details: dict[str, Any]) -> CliFailure:
    return CliFailure(
        code=ErrorCode.USAGE_ERROR,
        message=message,
        exit_code=ExitCode.USAGE_ERROR,
        fix=fix,
        details=details,
        next_actions=[
            action("help", ["infralink", "help", "host", "create"], "Show host create help")
        ],
    )


@host.command(name="create")
@click.option("--name", required=True, type=str, help="Canonical host name")
@click.option("--address", required=True, type=str, help="IP address or DNS hostname")
@click.option("--write", "write", is_flag=True, help="Write the scaffold into a directory registry")
@pass_context
def host_create(ctx: Context, name: str, address: str, write: bool) -> None:
    """Create a dry-run host manifest scaffold, or write it with --write."""
    address_field, normalized_address = _host_address(address)
    host_id = str(uuid4())
    host_data = {
        "canonical_name": name,
        "status": "provisioning",
        address_field: normalized_address,
    }

    from infralink.core.schema import HostSchema

    HostSchema(**host_data)
    manifest = {"hosts": {host_id: host_data}}
    manifest_path: Path | None = None
    mode = "dry_run"

    if write:
        if ctx.registry_path is None or not ctx.registry_path.is_dir():
            raise _host_create_failure(
                "Host create --write requires a directory registry",
                "Provide --registry pointing to a local hosts directory",
                {"registry": str(ctx.registry_path) if ctx.registry_path is not None else None},
            )
        registry = ctx.registry
        if registry.get_by_name(name) is not None:
            raise _host_create_failure(
                "Host canonical name already exists",
                "Choose a unique --name or update the existing host manifest",
                {"name": name},
            )
        manifest_path = ctx.registry_path / host_id / "manifest.yml"
        if manifest_path.parent.exists():
            raise _host_create_failure(
                "Generated host UUID already exists",
                "Run host create again to generate a new host UUID",
                {"host_id": host_id},
            )
        manifest_path.parent.mkdir(mode=0o755)
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        mode = "written"

    result = {
        "mode": mode,
        "host_id": host_id,
        "address": {
            "field": address_field,
            "value": normalized_address,
            "reason": (
                "input is an IP address"
                if address_field == "tailscale_ip"
                else "input is a DNS hostname and maps to tailscale_name"
            ),
        },
        "manifest_path": str(manifest_path) if manifest_path is not None else None,
        "manifest": manifest,
    }
    actions = [action("help", ["infralink", "help", "host", "show"], "Show host details help")]
    if manifest_path is not None:
        actions.append(
            action(
                "show",
                [*_root_source_argv(ctx), "host", "show", host_id],
                "Show the created host",
            )
        )
    _emit(ok_envelope(_context_for(path=["host", "create"]), result, actions))


@host.command(name="list")
@pass_context
def host_list(ctx: Context) -> None:
    """List all hosts in registry."""
    _emit_host_list(ctx, path=["host", "list"])


@host.command(name="show")
@click.argument("host_id")
@_page_options
@pass_context
def host_show(
    ctx: Context,
    host_id: str,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Show one host."""
    from infralink.cli.queries import show_host

    selected = _active_collection(collection, cursor, ("services", "projects"))
    fingerprint = _topology_fingerprint(
        ctx,
        include_registry=True,
        include_edges=False,
        identifiers={"host_id": host_id},
    )
    offset = _page_offset(
        command="host show",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = show_host(
        ctx.registry,
        host_id,
        collection=selected,
        limit=limit,
        offset=offset,
    )
    _attach_next_cursors(
        result,
        command="host show",
        collections=("services", "projects"),
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    _emit_query_result(
        ctx=ctx,
        path=["host", "show"],
        command_argv=["host", "show", host_id],
        result=result,
        limit=limit,
    )


@host.command(name="bootstrap")
@click.argument("host_id")
@click.option(
    "--plan",
    "plan_only",
    is_flag=True,
    help="Emit a read-only bootstrap plan.",
)
@click.option("--apply", "apply_changes", is_flag=True, help="Apply only failed host baseline actions.")
@pass_context
def host_bootstrap(ctx: Context, host_id: str, plan_only: bool, apply_changes: bool) -> int:
    """Plan required host bootstrap actions without applying them."""
    target = ctx.registry.get(host_id)
    if target is None:
        raise entity_not_found("host", host_id)
    readiness = evaluate_host_readiness(target, SshReadinessTransport())
    if plan_only == apply_changes:
        raise click.UsageError("pass exactly one of --plan or --apply")
    automated_actions = [
        item.id for item in readiness.actions if item.id not in _MANUAL_BOOTSTRAP_ACTIONS
    ]
    if apply_changes and automated_actions:
        control_root = Path("/opt/infra")
        playbook = control_root / "ansible/playbooks/infralink_host_baseline.yml"
        if not playbook.is_file():
            raise CliFailure(
                code=ErrorCode.CONFIGURATION_REQUIRED,
                message="Bastion host-bootstrap capability is not installed",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Install the current infra-management host-bootstrap capability on Bastion",
                details={"capability": "host_bootstrap"},
            )
        address = target.tailscale_ip or target.public_ip
        if not address:
            raise CliFailure(
                code=ErrorCode.CONFIGURATION_REQUIRED,
                message="Host address is required for bootstrap",
                exit_code=ExitCode.INPUT_ERROR,
                fix="Declare a Tailscale or public address for the host",
                details={"host": target.uuid},
            )
        completed = subprocess.run(
            [
                "ansible-playbook", "-i", f"{address},", "-u", "root", str(playbook),
                "-e", f"host_address={address}", "-e", f"host_uuid={target.uuid}", "-e", f"canonical_name={target.canonical_name}",
                "-e", json.dumps({"bootstrap_actions": automated_actions}),
            ],
            cwd=control_root, text=True, capture_output=True, check=False,
        )
        if completed.returncode != 0:
            raise CliFailure(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Host baseline apply failed",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Inspect Bastion Ansible logs and rerun host bootstrap --apply",
                details={"host": target.uuid},
            )
        readiness = evaluate_host_readiness(target, SshReadinessTransport())
    result = HostBootstrapPlanResult(
        host=DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name),
        readiness=readiness,
    )
    _emit(
        ok_envelope(
            _context_for(path=["host", "bootstrap"]),
            result,
            [
                action(
                    "reinspect-readiness",
                    [*_root_source_argv(ctx), "host", "bootstrap", target.uuid, "--plan"],
                    "Reinspect live host readiness",
                )
            ],
        )
    )
    return 0 if readiness.ready or plan_only else 1


@click.group()
def service() -> None:
    """Inspect services."""


@service.command(name="list")
@pass_context
def service_list(ctx: Context) -> None:
    """List services declared by registry hosts."""
    _emit_service_list(ctx, path=["service", "list"])


@service.command(name="show")
@click.argument("service_id")
@click.option("--app", "app_id", type=str, default=None)
@_page_options
@pass_context
def service_show(
    ctx: Context,
    service_id: str,
    app_id: str | None,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Show one service."""
    from infralink.cli.queries import show_service

    collections = ("hosts", "ports", "protocols", "edges")
    selected = _active_collection(collection, cursor, collections)
    identifiers = {"service_id": service_id}
    if app_id is not None:
        identifiers["app_id"] = app_id
    fingerprint = _topology_fingerprint(
        ctx,
        include_registry=True,
        include_edges=True,
        identifiers=identifiers,
    )
    offset = _page_offset(
        command="service show",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = show_service(
        ctx.registry,
        ctx.edges,
        service_id,
        app_id=app_id,
        collection=selected,
        limit=limit,
        offset=offset,
    )
    _attach_next_cursors(
        result,
        command="service show",
        collections=collections,
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    health_edges = [edge.id for edge in result.edges.items]
    actions = []
    if health_edges:
        actions.append(
            action(
                "check",
                [
                    *_root_source_argv(ctx),
                    "check",
                    *[argument for edge_id in health_edges for argument in ("--edge", edge_id)],
                ],
                "Run direct health checks for these related edges",
            )
        )
    _emit_query_result(
        ctx=ctx,
        path=["service", "show"],
        command_argv=[
            "service",
            "show",
            service_id,
            *([] if app_id is None else ["--app", app_id]),
        ],
        result=result,
        limit=limit,
        extra_actions=actions,
    )


@click.group()
def edge() -> None:
    """Inspect edges."""


@edge.command(name="list")
@pass_context
def edge_list(ctx: Context) -> None:
    """List all declared edges."""
    _emit_edge_list(ctx, path=["edge", "list"])


@edge.command(name="show")
@click.argument("edge_id")
@_page_options
@pass_context
def edge_show(
    ctx: Context,
    edge_id: str,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Show one edge."""
    from infralink.cli.queries import show_edge

    selected = _active_collection(collection, cursor, ("secret_refs",))
    fingerprint = _topology_fingerprint(
        ctx,
        include_registry=False,
        include_edges=True,
        identifiers={"edge_id": edge_id},
    )
    offset = _page_offset(
        command="edge show",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = show_edge(
        ctx.edges,
        edge_id,
        limit=limit,
        offset=offset,
    )
    _attach_next_cursors(
        result,
        command="edge show",
        collections=("secret_refs",),
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    _emit_query_result(
        ctx=ctx,
        path=["edge", "show"],
        command_argv=["edge", "show", edge_id],
        result=result,
        limit=limit,
        extra_actions=[
            action(
                "check",
                [*_root_source_argv(ctx), "check", "--edge", edge_id],
                "Run a direct health check for this edge",
            )
        ],
    )


def main(args: list[str] | None = None) -> int:
    return int(cli.main(args=args, prog_name="infralink", standalone_mode=False))


def run(args: list[str] | None = None) -> None:
    raise SystemExit(main(args))


if __name__ == "__main__":
    run()
