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
    RootResult,
    VersionResult,
)
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.output import command_context, error_envelope, ok_envelope

# Default paths (can be overridden)
DEFAULT_REGISTRY = "examples/registry.yml"
DEFAULT_EDGES = "examples/edges.yml"
_INVOCATION_ARGS: ContextVar[list[str] | None] = ContextVar(
    "infralink_invocation_args", default=None
)


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

            if self.registry_path and self.registry_path.exists():
                if self.registry_path.is_dir():
                    self._registry = Registry.load_dir(self.registry_path)
                else:
                    self._registry = Registry.load(self.registry_path)
            else:
                path = str(self.registry_path)
                raise CliFailure(
                    code=ErrorCode.INPUT_LOAD_FAILED,
                    message="Registry could not be loaded",
                    exit_code=3,
                    fix="Provide an existing registry with --registry",
                    details={"source": "registry", "path": path},
                    next_actions=[
                        action(
                            "help",
                            ["infralink", "help", "validate"],
                            "Show validation input options",
                        )
                    ],
                )
        return self._registry

    @property
    def edges(self) -> Any:
        """Lazy-load edges."""
        if self._edges is None:
            from infralink.core.edges import EdgeSet

            if self.edges_path and self.edges_path.exists():
                self._edges = EdgeSet.load(self.edges_path)
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
                        with self.registry_path.open() as f:
                            data = yaml.safe_load(f)
                        self._edges = EdgeSet.from_registry(data)
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
    "resolve": {"description": "Resolve an edge to targets.", "usage": "infralink resolve <edge-id>"},
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
        "description": "Inspect hosts.",
        "arguments": [],
        "options": [],
        "examples": ["infralink host show host-1"],
    },
    ("host", "show"): {
        "description": "Show one host.",
        "arguments": [{"name": "host_id", "type": "string", "required": True}],
        "options": [],
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
        "options": [],
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
        "options": [],
        "examples": ["infralink service show api"],
    },
    ("secrets",): {
        "description": "Inspect and audit secret references.",
        "arguments": [],
        "options": [],
        "examples": ["infralink secrets inspect"],
    },
    ("secrets", "inspect"): {
        "description": "Inspect declared secret references.",
        "arguments": [],
        "options": [{"name": "ref", "type": "string", "required": False}],
        "examples": ["infralink secrets inspect"],
    },
    ("secrets", "audit"): {
        "description": "Audit secret references against a provider.",
        "arguments": [],
        "options": [{"name": "provider", "type": "string", "required": False}],
        "examples": ["infralink secrets audit"],
    },
}


def _context_for(
    argv: list[str] | None = None, path: list[str] | None = None
) -> CommandContext:
    active_argv = argv
    if active_argv is None:
        active_argv = _INVOCATION_ARGS.get() or []
    return command_context(
        ["infralink", *active_argv],
        path=path if path is not None else _command_path(active_argv),
        args={},
        flags=[item for item in active_argv if item.startswith("-")],
        resolved={"version": __version__, "cwd": os.getcwd()},
    )


def _command_path(argv: list[str]) -> list[str]:
    path: list[str] = []
    index = 0
    options_with_values = {"-r", "--registry", "-e", "--edges", "-o", "--output"}
    while index < len(argv):
        item = argv[index]
        if item in options_with_values:
            index += 2
            continue
        if item.startswith("-"):
            index += 1
            continue
        path.append(item)
        index += 1
    return path[:2]


def _normalize_discovery_aliases(argv: list[str]) -> list[str]:
    if "--version" in argv:
        index = argv.index("--version")
        if index == len(argv) - 1:
            return [*argv[:index], "version"]
    if "--help" not in argv:
        return argv

    help_index = argv.index("--help")
    prefix = argv[:help_index]
    global_prefix: list[str] = []
    path: list[str] = []
    index = 0
    options_with_values = {"-r", "--registry", "-e", "--edges", "-o", "--output"}
    while index < len(prefix):
        item = prefix[index]
        if not path and item in options_with_values and index + 1 < len(prefix):
            global_prefix.extend(prefix[index : index + 2])
            index += 2
            continue
        if not path and item == "--verbose":
            global_prefix.append(item)
        else:
            path.append(item)
        index += 1
    return [*global_prefix, "help", *path]


def _emit(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, separators=(",", ":")))


def _help_result(path: tuple[str, ...]) -> HelpResult:
    metadata = HELP_METADATA.get(path)
    if metadata is None and len(path) == 1 and path[0] in COMMAND_METADATA:
        command = _load_command(path[0])
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
                    options.append(
                        OptionDescriptor(
                            name=parameter.name or "",
                            type=parameter.type.name,
                            required=parameter.required,
                        )
                    )
            metadata = {
                "description": COMMAND_METADATA[path[0]]["description"],
                "arguments": arguments,
                "options": options,
                "examples": [COMMAND_METADATA[path[0]]["usage"]],
            }
    if metadata is None:
        raise click.UsageError("Unknown command path")
    return HelpResult(path=list(path), **metadata)


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
        return _discovery_command("services")
    if name in {"host", "edge", "service", "secrets"}:
        children = {
            "host": ("show",),
            "edge": ("show",),
            "service": ("show",),
            "secrets": ("inspect", "audit"),
        }[name]
        return _discovery_group(name, children)
    return None


class JsonGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(COMMAND_METADATA.keys())

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
        exit_code = 0
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
                next_actions=[
                    action("help", ["infralink", "help"], "Show available commands")
                ],
            )
            _emit(error_envelope(_context_for(incoming), usage_failure))
            exit_code = usage_failure.exit_code
        except CliFailure as cli_failure:
            payload = error_envelope(_context_for(incoming), cli_failure)
            if (
                "validate" in _command_path(incoming)
                and any(item in {"-o", "--output"} for item in incoming)
            ):
                payload["status"] = "error"
            _emit(payload)
            exit_code = cli_failure.exit_code
        except Exception:
            internal_failure = CliFailure(
                code=ErrorCode.INTERNAL_ERROR,
                message="An unexpected internal error occurred",
                exit_code=70,
                fix="Retry the command or report the failure",
                next_actions=[],
            )
            _emit(error_envelope(_context_for(incoming), internal_failure))
            exit_code = internal_failure.exit_code

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


def _discovery_command(name: str) -> click.Command:
    @click.command(name=name)
    def command() -> None:
        _emit_help((name,))

    return command


def _discovery_group(name: str, children: tuple[str, ...]) -> click.Group:
    @click.group(name=name, invoke_without_command=True)
    def group() -> None:
        if click.get_current_context().invoked_subcommand is None:
            _emit_help((name,))

    for child in children:
        metadata = HELP_METADATA[(name, child)]
        arguments = metadata["arguments"]

        def callback(
            *values: str,
            _path: tuple[str, str] = (name, child),
            **options: Any,
        ) -> None:
            del values, options
            _emit_help(_path)

        params: list[click.Parameter] = [
            click.Argument([argument["name"]], required=argument["required"])
            for argument in arguments
        ]
        group.add_command(click.Command(child, callback=callback, params=params))
    return group


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

    command_tree = RootResult(
        version=__version__,
        commands=[
            CommandDescriptor(
                name=name,
                description=meta.get("description", ""),
                usage=meta.get("usage", f"infralink {name}"),
            )
            for name, meta in sorted(COMMAND_METADATA.items())
        ],
    )

    payload = ok_envelope(
        _context_for(path=[]),
        command_tree,
        [
            action("help", ["infralink", "help"], "Show available commands"),
            action("validate", ["infralink", "validate"], "Validate registry and edges"),
            action("list", ["infralink", "edges-list"], "List all edges"),
        ],
    )
    _emit(payload)


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
        click.echo(json.dumps(payload))
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
    click.echo(json.dumps(payload))


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
        click.echo(json.dumps(payload))
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
    click.echo(json.dumps(payload))


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
        click.echo(json.dumps(payload))
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
    click.echo(json.dumps(payload))


def main(args: list[str] | None = None) -> int:
    return int(cli.main(args=args, prog_name="infralink", standalone_mode=False))


def run(args: list[str] | None = None) -> None:
    raise SystemExit(main(args))


if __name__ == "__main__":
    run()
