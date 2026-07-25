"""Main CLI entry point for infralink."""

from __future__ import annotations

import json
import os
import shlex
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
    CommandContext,
    CommandDescriptor,
    HelpResult,
    OptionDescriptor,
    PageInfo,
    RootResult,
    ServiceListResult,
    ServiceSummary,
    VersionResult,
)
from infralink.cli.errors import CliFailure, ErrorCode
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


class Context:
    """CLI context object passed to commands."""

    def __init__(self) -> None:
        self.registry_path: Path | None = None
        self.edges_path: Path | None = None
        self.verbose: bool = False
        self.output: str = "json"
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
        "usage": "infralink analyze",
    },
    "check": {"description": "Run health checks for edges.", "usage": "infralink check"},
    "diagram": {
        "description": "Generate topology diagrams.",
        "usage": "infralink diagram",
    },
    "docs": {"description": "Generate documentation outputs.", "usage": "infralink docs"},
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
            {"name": "format", "type": "choice", "required": False},
            {"name": "prefer_ip", "type": "choice", "required": False},
        ],
        "examples": ["infralink resolve edge-1"],
    },
    ("app", "list"): {
        "description": "List application groupings.",
        "arguments": [],
        "options": [],
        "examples": ["infralink app list"],
    },
    ("app", "show"): {
        "description": "Show one application grouping.",
        "arguments": [{"name": "app_id", "type": "string", "required": True}],
        "options": [],
        "examples": ["infralink app show core"],
    },
    ("host",): {
        "description": "Unavailable until host detail commands are implemented.",
        "arguments": [],
        "options": [],
        "examples": ["infralink host show host-1"],
    },
    ("host", "show"): {
        "description": "Unavailable until host detail commands are implemented.",
        "arguments": [{"name": "host_id", "type": "string", "required": True}],
        "options": [],
        "examples": ["infralink host show host-1"],
    },
    ("edge",): {
        "description": "Unavailable until edge detail commands are implemented.",
        "arguments": [],
        "options": [],
        "examples": ["infralink edge show edge-1"],
    },
    ("edge", "show"): {
        "description": "Unavailable until edge detail commands are implemented.",
        "arguments": [{"name": "edge_id", "type": "string", "required": True}],
        "options": [],
        "examples": ["infralink edge show edge-1"],
    },
    ("service",): {
        "description": "Unavailable until service detail commands are implemented.",
        "arguments": [],
        "options": [],
        "examples": ["infralink service show api"],
    },
    ("service", "show"): {
        "description": "Unavailable until service detail commands are implemented.",
        "arguments": [{"name": "service_id", "type": "string", "required": True}],
        "options": [],
        "examples": ["infralink service show api"],
    },
    ("secrets",): {
        "description": "Unavailable until secret commands are implemented.",
        "arguments": [],
        "options": [],
        "examples": ["infralink secrets inspect"],
    },
    ("secrets", "inspect"): {
        "description": "Unavailable until secret commands are implemented.",
        "arguments": [],
        "options": [{"name": "ref", "type": "string", "required": False}],
        "examples": ["infralink secrets inspect"],
    },
    ("secrets", "audit"): {
        "description": "Unavailable until secret commands are implemented.",
        "arguments": [],
        "options": [{"name": "provider", "type": "string", "required": False}],
        "examples": ["infralink secrets audit"],
    },
}


def _context_for(argv: list[str] | None = None, path: list[str] | None = None) -> CommandContext:
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
    return command_context(
        ["infralink", *redacted_argv],
        path=path if path is not None else parsed_path,
        args=parsed_args,
        flags=[item for item in redacted_argv if item.startswith("-")],
        resolved=resolved,
    )


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
    _ENVELOPE_EMITTED.set(True)
    click.echo(json.dumps(payload, separators=(",", ":")))


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
    discovery = {
        "host": ["infralink", "hosts"],
        "service": ["infralink", "services"],
        "edge": ["infralink", "edges-list"],
        "app": ["infralink", "app", "list"],
    }[entity_type]
    return CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message=f"{entity_type.title()} not found",
        exit_code=3,
        fix=f"Run {shlex.join(discovery)}",
        details={"entity_type": entity_type, "requested_id": requested_id},
        next_actions=[action("list", discovery, f"List {entity_type} records")],
    )


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
        exit_code = 0
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
                    exit_code = result
            except click.UsageError:
                usage_failure = CliFailure(
                    code=ErrorCode.USAGE_ERROR,
                    message="Invalid command usage",
                    exit_code=2,
                    fix="Run infralink help",
                    next_actions=[action("help", ["infralink", "help"], "Show available commands")],
                )
                if not _ENVELOPE_EMITTED.get():
                    _emit(error_envelope(_context_for(incoming), usage_failure))
                exit_code = usage_failure.exit_code
            except CliFailure as cli_failure:
                if not _ENVELOPE_EMITTED.get():
                    _emit(error_envelope(_context_for(incoming), cli_failure))
                exit_code = cli_failure.exit_code
            except SystemExit as system_exit:
                if _ENVELOPE_EMITTED.get() and isinstance(system_exit.code, int):
                    exit_code = system_exit.code
                else:
                    internal_failure = CliFailure(
                        code=ErrorCode.INTERNAL_ERROR,
                        message="An unexpected internal error occurred",
                        exit_code=70,
                        fix="Retry the command or report the failure",
                        next_actions=[],
                    )
                    _emit(error_envelope(_context_for(incoming), internal_failure))
                    exit_code = internal_failure.exit_code
            except Exception:
                internal_failure = CliFailure(
                    code=ErrorCode.INTERNAL_ERROR,
                    message="An unexpected internal error occurred",
                    exit_code=70,
                    fix="Retry the command or report the failure",
                    next_actions=[],
                )
                if not _ENVELOPE_EMITTED.get():
                    _emit(error_envelope(_context_for(incoming), internal_failure))
                exit_code = internal_failure.exit_code
        finally:
            _ENVELOPE_EMITTED.reset(emitted_token)
            _INVOCATION_ARGS.reset(invocation_token)
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
    type=click.Choice(["json"], case_sensitive=False),
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


@cli.command()
@pass_context
def services(ctx: Context) -> None:
    """List services declared by registry hosts."""
    registry = ctx.registry
    service_hosts: dict[str, set[str]] = {}
    service_ports: dict[str, set[int]] = {}
    service_protocols: dict[str, set[str]] = {}
    for host in registry:
        for service_id in set(host.roles) | set(host.service_names):
            service_hosts.setdefault(service_id, set()).add(host.uuid)
            config = host.services.get(service_id, {})
            port = config.get("port")
            if isinstance(port, int):
                service_ports.setdefault(service_id, set()).add(port)
            protocol = config.get("protocol")
            if isinstance(protocol, str):
                service_protocols.setdefault(service_id, set()).add(protocol)

    edges = ctx.edges
    for edge in edges:
        service_hosts.setdefault(edge.target_service, set()).add(edge.target_host)
        service_ports.setdefault(edge.target_service, set()).add(edge.target_port)
        if edge.protocol:
            service_protocols.setdefault(edge.target_service, set()).add(edge.protocol)

    items = []
    for service_id in sorted(service_hosts):
        hosts = sorted(service_hosts[service_id])
        ports = sorted(service_ports.get(service_id, set()))
        protocols = sorted(service_protocols.get(service_id, set()))
        items.append(
            ServiceSummary(
                id=service_id,
                host_count=len(hosts),
                host_ids=hosts[:128],
                hosts_truncated=len(hosts) > 128,
                port_count=len(ports),
                ports=ports[:64],
                ports_truncated=len(ports) > 64,
                protocol_count=len(protocols),
                protocols=protocols[:32],
                protocols_truncated=len(protocols) > 32,
            )
        )
    result = ServiceListResult(
        items=items,
        page=PageInfo(
            limit=min(max(100, len(items)), 1000),
            returned=len(items),
            total=len(service_hosts),
            next_cursor=None,
        ),
    )
    _emit(
        ok_envelope(
            _context_for(path=["services"]),
            result,
            [action("help", ["infralink", "help", "services"], "Show service help")],
        )
    )


@cli.command()
@pass_context
def info(ctx: Context) -> None:
    """Show registry and edge summary."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
        edges = ctx.edges
    except CliFailure:
        raise
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "INFO_FAILED",
            "Ensure registry/edges paths are correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        _emit(payload)
        raise SystemExit(1) from exc

    from infralink.core.schema import EdgeType

    edge_types = []
    for etype in EdgeType:
        count = len(edges.by_type(etype))
        if count > 0:
            edge_types.append({"type": etype.value, "count": count})

    result = {
        "version": __version__,
        "registry": {
            "path": str(ctx.registry_path),
            "total_hosts": len(registry),
            "active_hosts": len(registry.active_hosts()),
            "groups": sorted(registry.groups()),
            "clouds": sorted(registry.clouds()),
        },
        "edges": {
            "path": str(ctx.edges_path),
            "total_edges": len(edges),
            "critical_edges": len(edges.critical_edges()),
            "by_type": edge_types,
        },
    }
    payload = ok_envelope(
        command,
        result,
        [
            {"command": "infralink hosts", "description": "List all hosts"},
            {"command": "infralink edges-list", "description": "List all edges"},
        ],
    )
    _emit(payload)


@cli.command()
@pass_context
def hosts(ctx: Context) -> None:
    """List all hosts in registry."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
    except CliFailure:
        raise
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "HOSTS_FAILED",
            "Ensure registry path is correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        _emit(payload)
        raise SystemExit(1) from exc

    hosts_payload = []
    for host in sorted(registry, key=lambda h: h.canonical_name):
        hosts_payload.append(
            {
                "name": host.canonical_name,
                "uuid": host.uuid,
                "status": host.status.value,
                "group": host.group,
                "cloud": host.cloud,
                "tailscale_ip": host.tailscale_ip,
            }
        )

    result = {"hosts": hosts_payload, "count": len(hosts_payload)}
    payload = ok_envelope(
        command,
        result,
        [
            {"command": "infralink info", "description": "Show registry summary"},
            {"command": "infralink edges-list", "description": "List all edges"},
        ],
    )
    payload.update(result)
    _emit(payload)


@cli.command()
@pass_context
def edges_list(ctx: Context) -> None:
    """List all declared edges."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        edges = ctx.edges
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "EDGES_FAILED",
            "Ensure edges path is correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        _emit(payload)
        raise SystemExit(1) from exc

    edge_payload = []
    for edge in edges:
        sources = len(edge.source_hosts) if not edge.is_wildcard_source() else "*"
        edge_payload.append(
            {
                "id": edge.id,
                "type": edge.type.value,
                "target_service": edge.target_service,
                "target_port": edge.target_port,
                "criticality": edge.criticality.value,
                "sources": sources,
            }
        )

    payload = ok_envelope(
        command,
        {"edges": edge_payload, "count": len(edge_payload)},
        [
            {"command": "infralink info", "description": "Show registry summary"},
            {"command": "infralink resolve <edge-id>", "description": "Resolve an edge"},
        ],
    )
    _emit(payload)


def main(args: list[str] | None = None) -> int:
    return int(cli.main(args=args, prog_name="infralink", standalone_mode=False))


def run(args: list[str] | None = None) -> None:
    raise SystemExit(main(args))


if __name__ == "__main__":
    run()
