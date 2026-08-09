"""Main CLI entry point for infralink."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import click

from infralink import __version__
from infralink.cli.actions import action
from infralink.cli.contracts import (
    ArgumentDescriptor,
    Binding,
    CommandContext,
    CommandDescriptor,
    EdgeSummary,
    HelpResult,
    HostSummary,
    InfoResult,
    InfoSources,
    InfoSummary,
    OptionDescriptor,
    RootResult,
    ServiceSummary,
    VersionResult,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode, internal_failure
from infralink.cli.output import (
    command_context,
    error_envelope,
    ok_envelope,
    redact_argv,
)

# Default paths (can be overridden)
DEFAULT_REGISTRY = "examples/registry.yml"
DEFAULT_EDGES = "examples/edges.yml"
_INVOCATION_ARGS: ContextVar[list[str] | None] = ContextVar(
    "infralink_invocation_args", default=None
)
_ENVELOPE_EMITTED: ContextVar[bool] = ContextVar("infralink_envelope_emitted", default=False)
_DEFER_ENVELOPE: ContextVar[bool] = ContextVar("infralink_defer_envelope", default=False)
_PENDING_ENVELOPE: ContextVar[str | None] = ContextVar("infralink_pending_envelope", default=None)


class Context:
    """CLI context object passed to commands."""

    def __init__(self) -> None:
        self.registry_path: Path | None = None
        self.edges_path: Path | None = None
        self.verbose: bool = False
        self.output: str = "json"
        self.output_explicit: bool = False
        self._registry: Any = None
        self._edges: Any = None

    @property
    def registry(self) -> Any:
        """Lazy-load registry."""
        if self._registry is None:
            from infralink.core.registry import Registry

            path = str(self.registry_path)
            if not self.registry_path or not self.registry_path.exists():
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

            if self.edges_path and self.edges_path.exists():
                path = str(self.edges_path)
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
                        # For directory-based registry, we should probably look for a unified edges file
                        # or just rely on EdgeSet initialization from the already-loaded registry.
                        # Actually, EdgeSet.from_registry expects a dict.
                        # I will check if registry is already loaded.
                        if self._registry:
                            # This is tricky because self._registry is a Registry object, not a dict.
                            # But Registry has an applications property, etc.
                            # For now, if it is a directory, we just default to empty if edges.yml is missing.
                            self._edges = EdgeSet([])
                        else:
                            self._edges = EdgeSet([])
                    else:
                        try:
                            with self.registry_path.open() as f:
                                data = yaml.safe_load(f)
                            self._edges = EdgeSet.from_registry(data)
                        except Exception:
                            raise input_load_failed("registry", str(self.registry_path)) from None
                else:
                    self._edges = EdgeSet([])
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
    "hosts": {"description": "List all hosts.", "usage": "infralink hosts"},
    "services": {"description": "List all services.", "usage": "infralink services"},
    "edges-list": {"description": "List all edges.", "usage": "infralink edges-list"},
    "host": {"description": "Inspect hosts.", "usage": "infralink host show <host-id>"},
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
        "registry": str(root_values.get("registry", DEFAULT_REGISTRY)),
        "edges": str(root_values.get("edges", DEFAULT_EDGES)),
        "output": root_values.get("output", "json"),
        "verbose": bool(root_values.get("verbose", False)),
    }
    resolved.update(_command_resolved_overrides(redacted_argv, parsed_path))
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
                ["infralink", "help", "validate"],
                "Show validation input options",
            )
        ],
    )


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
    return ["help", *path]


def _emit(payload: dict[str, Any]) -> None:
    serialized = json.dumps(payload, separators=(",", ":"))
    _ENVELOPE_EMITTED.set(True)
    if _DEFER_ENVELOPE.get():
        _PENDING_ENVELOPE.set(serialized)
    else:
        click.echo(serialized)


def _help_result(path: tuple[str, ...]) -> HelpResult:
    metadata = HELP_METADATA.get(path)
    command = _command_for_path(path)
    if command is not None:
        arguments = []
        options = []
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
        root_metadata = COMMAND_METADATA.get(path[0], {}) if path else {}
        metadata = {
            "description": (
                (metadata or {}).get("description")
                or command.help
                or root_metadata.get("description", "")
            ),
            "arguments": arguments,
            "options": options,
            "examples": (metadata or {}).get("examples", [root_metadata.get("usage", "infralink")]),
        }
    if metadata is None:
        raise click.UsageError("Unknown command path")
    return HelpResult(path=list(path), **metadata)


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
                    next_actions=[
                        action(
                            "help",
                            [
                                "infralink",
                                "help",
                                *([artifact_command] if artifact_command is not None else []),
                            ],
                            "Show command usage",
                        )
                    ],
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
    default=DEFAULT_REGISTRY,
    help="Path to registry YAML file",
)
@click.option(
    "-e",
    "--edges",
    type=click.Path(exists=False, path_type=Path),
    default=DEFAULT_EDGES,
    help="Path to edges YAML file",
)
@click.option("-v", "--verbose", is_flag=True, help="Enable verbose output")
@click.option(
    "-o",
    "--output",
    type=click.Choice(["json", "yaml"], case_sensitive=False),
    default="json",
    show_default=True,
    help="Output format (json = agent-first)",
)
@pass_context
def cli(ctx: Context, registry: Path, edges: Path, verbose: bool, output: str) -> None:
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
            action("help", ["infralink", "help"], "Show available commands"),
            action("list", ["infralink", "services"], "List declared services"),
            action("version", ["infralink", "version"], "Show CLI version"),
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
    return [
        "infralink",
        "--registry",
        str(ctx.registry_path),
        "--edges",
        str(ctx.edges_path),
    ]


def _summary_detail_actions(
    ctx: Context,
    result: Any,
    path: list[str],
    command_argv: list[str],
) -> list[Any]:
    summaries: list[Any]
    scoped_app_id: str | None = None
    if path in (["hosts"], ["services"], ["edges-list"]):
        summaries = result.items
    elif path == ["app", "show"]:
        summaries = result.services.items
        scoped_app_id = command_argv[2]
    else:
        return []

    actions = []
    seen: set[tuple[str, str]] = set()
    for summary in summaries:
        if isinstance(summary, HostSummary):
            truncated = summary.services_truncated or summary.projects_truncated
            entity = "host"
        elif isinstance(summary, ServiceSummary):
            truncated = (
                summary.hosts_truncated or summary.ports_truncated or summary.protocols_truncated
            )
            entity = "service"
        elif isinstance(summary, EdgeSummary):
            truncated = summary.secret_refs_truncated
            entity = "edge"
        else:
            continue
        identity = (entity, summary.id)
        if not truncated or identity in seen:
            continue
        if scoped_app_id is not None and entity != "service":
            continue
        seen.add(identity)
        command = [*_root_source_argv(ctx), entity, "show", summary.id]
        if scoped_app_id is not None:
            command.extend(["--app", scoped_app_id])
        actions.append(
            action(
                "show",
                command,
                f"Show complete {entity} details",
            )
        )
    return actions


def _emit_query_result(
    *,
    ctx: Context,
    path: list[str],
    command_argv: list[str],
    result: Any,
    limit: int,
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
    actions = [action("help", ["infralink", "help", *path], f"Show {' '.join(path)} help")]
    actions.extend(_summary_detail_actions(ctx, result, path, command_argv))
    actions.extend(extra_actions or [])
    for collection, page, source in pages:
        if page.next_cursor is None:
            continue
        actions.append(
            action(
                "continue",
                [
                    *_root_source_argv(ctx),
                    *command_argv,
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
        default=100,
        show_default=True,
    )(command)


@click.command()
@_page_options
@pass_context
def services(
    ctx: Context,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """List services declared by registry hosts."""
    from infralink.cli.queries import list_services

    selected = _active_collection(collection, cursor, ("items",))
    fingerprint = _topology_fingerprint(ctx, include_registry=True, include_edges=True)
    offset = _page_offset(
        command="services",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = list_services(
        ctx.registry,
        ctx.edges,
        limit=limit,
        offset=offset,
    )
    _attach_next_cursors(
        result,
        command="services",
        collections=("items",),
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    _emit_query_result(
        ctx=ctx,
        path=["services"],
        command_argv=["services"],
        result=result,
        limit=limit,
    )


@cli.command()
@pass_context
def info(ctx: Context) -> None:
    """Show registry and edge summary."""
    from infralink.cli.queries import list_services

    registry = ctx.registry
    edges = ctx.edges
    service_count = list_services(registry, edges, limit=1).page.total
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
            action("list", [*_root_source_argv(ctx), "hosts"], "List all hosts"),
            action("list", [*_root_source_argv(ctx), "edges-list"], "List all edges"),
        ],
    )
    _emit(payload)


@click.command()
@_page_options
@pass_context
def hosts(
    ctx: Context,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """List all hosts in registry."""
    from infralink.cli.queries import list_hosts

    selected = _active_collection(collection, cursor, ("items",))
    fingerprint = _topology_fingerprint(ctx, include_registry=True, include_edges=False)
    offset = _page_offset(
        command="hosts",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = list_hosts(
        ctx.registry,
        limit=limit,
        offset=offset,
    )
    _attach_next_cursors(
        result,
        command="hosts",
        collections=("items",),
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    _emit_query_result(
        ctx=ctx,
        path=["hosts"],
        command_argv=["hosts"],
        result=result,
        limit=limit,
    )


@click.command(name="edges-list")
@_page_options
@pass_context
def edges_list(
    ctx: Context,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """List all declared edges."""
    from infralink.cli.queries import list_edges

    selected = _active_collection(collection, cursor, ("items",))
    fingerprint = _topology_fingerprint(ctx, include_registry=False, include_edges=True)
    offset = _page_offset(
        command="edges-list",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = list_edges(
        ctx.edges,
        limit=limit,
        offset=offset,
    )
    _attach_next_cursors(
        result,
        command="edges-list",
        collections=("items",),
        selected=selected,
        offset=offset,
        limit=limit,
        fingerprint=fingerprint,
    )
    _emit_query_result(
        ctx=ctx,
        path=["edges-list"],
        command_argv=["edges-list"],
        result=result,
        limit=limit,
    )


@click.group()
def host() -> None:
    """Inspect hosts."""


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


@click.group()
def service() -> None:
    """Inspect services."""


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
