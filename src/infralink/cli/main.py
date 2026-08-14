"""Main CLI entry point for infralink."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import click
import yaml

from infralink import __version__
from infralink.cli.actions import action, redact_argv
from infralink.cli.contracts import (
    Action,
    ArgumentDescriptor,
    Binding,
    CommandContext,
    CommandDescriptor,
    DoctorTarget,
    HelpNavigationAction,
    HelpResult,
    HelpSubcommand,
    HostBootstrapAction,
    HostBootstrapPlanResult,
    HostBootstrapRequest,
    HostControllerBootstrapState,
    HostReadinessCheck,
    HostReadinessResult,
    InfoResult,
    InfoSources,
    InfoSummary,
    OptionDescriptor,
    RootResult,
    VersionResult,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode, internal_failure
from infralink.cli.host_readiness import evaluate_host_readiness
from infralink.cli.operation_contracts import (
    HostApplyPlan,
    HostApplyResult,
    HostDispatch,
    HostLogsResult,
    HostStatusResult,
    HostTimer,
    HostVerifierResult,
    LastReconcile,
    OperationStatusResult,
    OperationSummary,
    TargetReconcileStatus,
)
from infralink.cli.output import (
    command_context,
    error_envelope,
    ok_envelope,
)
from infralink.host_readiness import HostReadinessProbe
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
_CONTROL_ROOT = Path(os.environ.get("INFRALINK_CONTROL_ROOT", "/opt/infra"))
_CONTROLLER_REFRESH_PLAYBOOK = "ansible/playbooks/infralink_controller_refresh.yml"
_CONTROLLER_REFRESH_SOURCE_REMOTE = "https://github.com/relax-dot-gg/infra-management.git"


def _isolated_git_environment() -> dict[str, str]:
    """Run controller Git with only the managed root credential store enabled."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        # An empty helper resets lower-precedence repository-local helpers
        # before the managed root credential store is consulted.
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "store",
        "GIT_CONFIG_KEY_2": "credential.useHttpPath",
        "GIT_CONFIG_VALUE_2": "true",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _controller_remote_identity(remote: str) -> str:
    """Compare HTTPS controller remotes without retaining embedded credentials."""
    parsed = urlsplit(remote)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return remote
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


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
        "usage": (
            "infralink doctor [host|service|edge|profile <ref>] [--validate]; "
            "logical services use service <host-uuid>/<service-id>"
        ),
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
        "description": "Inspect, scaffold, bootstrap, or apply hosts.",
        "usage": "infralink host [create|list|show|bootstrap|apply]",
    },
    "operation": {
        "description": "Inspect durable host apply operations.",
        "usage": "infralink operation status OPERATION_ID",
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
    ("doctor",): {
        "description": "Inspect declared observation coverage and live evidence.",
        "arguments": [
            {"name": "target_type", "type": "choice", "required": False},
            {"name": "target_ref", "type": "string", "required": False},
        ],
        "options": [],
        "examples": [
            "infralink doctor host cyberstorm-watchtower",
            "infralink doctor service <host-uuid>/<logical-service-id>",
        ],
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
        "description": "Plan or apply a declared Tailnet host bootstrap.",
        "arguments": [{"name": "host_id", "type": "string", "required": True}],
        "options": [
            {"name": "ssh_host", "type": "text", "required": True},
            {"name": "bws_token_stdin", "type": "boolean", "required": False},
            {"name": "plan", "type": "boolean", "required": False},
            {"name": "apply", "type": "boolean", "required": False},
        ],
        "examples": [
            "infralink host bootstrap host-1 --ssh-host 100.64.0.1",
            "printf '%s\\n' \"$HOST_BWS_TOKEN\" | infralink host bootstrap host-1 --ssh-host 100.64.0.1 --bws-token-stdin --apply",
        ],
    },
    ("host", "apply"): {
        "description": "Start one declared host-local reconcile operation.",
        "arguments": [{"name": "host", "type": "string", "required": True}],
        "options": [
            {"name": "dry_run", "type": "boolean", "required": False},
            {"name": "wait", "type": "boolean", "required": False},
            {"name": "timeout", "type": "integer", "required": False},
        ],
        "examples": [
            "infralink host apply relaxgg-db-es1",
            "infralink host apply relaxgg-db-es1 --wait",
        ],
    },
    ("host", "status"): {
        "description": "Read target timer and latest reconcile evidence.",
        "arguments": [{"name": "host_ref", "type": "string", "required": True}],
        "options": [],
        "examples": ["infralink host status relaxgg-db-es1"],
    },
    ("host", "logs"): {
        "description": "Read bounded sanitized evidence from the target's latest reconcile run.",
        "arguments": [{"name": "host_ref", "type": "string", "required": True}],
        "options": [{"name": "last_run", "type": "boolean", "required": True}],
        "examples": ["infralink host logs relaxgg-db-es1 --last-run"],
    },
    ("operation",): {
        "description": "Inspect declared host-local reconcile operations.",
        "arguments": [],
        "options": [],
        "examples": [
            "infralink operation status ssh/32a3324f-c3d0-4a4f-9587-52c099bcb3fb/8d6c4ad6-0e4a-4b58-9fe3-5ad9e1760d56"
        ],
    },
    ("operation", "status"): {
        "description": "Get the current state of one declared host-local reconcile operation.",
        "arguments": [{"name": "operation_id", "type": "string", "required": True}],
        "options": [],
        "examples": [
            "infralink operation status ssh/32a3324f-c3d0-4a4f-9587-52c099bcb3fb/8d6c4ad6-0e4a-4b58-9fe3-5ad9e1760d56"
        ],
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


def _usage_actions(path: list[str], artifact_command: str | None) -> list[Action]:
    if artifact_command is not None:
        return [
            action(
                "help",
                [*_help_argv_prefix(), artifact_command],
                "Show command usage",
            )
        ]
    canonical_aliases: dict[tuple[str, ...], tuple[str, ...]] = {
        ("hosts",): ("host", "list"),
        ("services",): ("service", "list"),
        ("edges-list",): ("edge", "list"),
    }
    canonical_alias = canonical_aliases.get(tuple(path))
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
    if name == "operation":
        return operation
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


def _compatibility_action(ctx: Context, path: list[str], canonical: list[str]) -> list[Action]:
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
    host_data: dict[str, Any] = {
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
        assert ctx.registry_path is not None
        git_worktree = ctx.registry_path.parent
        result["write_state"] = "local_uncommitted"
        result["git_worktree"] = str(git_worktree)
        actions.append(
            action(
                "show",
                [*_root_source_argv(ctx), "host", "show", host_id],
                "Show the created host",
            )
        )
        actions.append(
            action(
                "git-status",
                ["git", "-C", str(git_worktree), "status", "--short"],
                "Inspect the uncommitted registry change",
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
    "--ssh-host",
    required=True,
    metavar="TAILNET_IPV4",
    help="Declared Tailnet IPv4 address used for the bootstrap SSH connection.",
)
@click.option(
    "--bws-token-stdin",
    is_flag=True,
    help="Read the host machine BWS token from standard input.",
)
@click.option(
    "--plan",
    "plan_only",
    is_flag=True,
    help="Emit a read-only bootstrap plan.",
)
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Apply failed host baseline actions or the declared V2 controller refresh.",
)
@pass_context
def host_bootstrap(
    ctx: Context,
    host_id: str,
    ssh_host: str,
    bws_token_stdin: bool,
    plan_only: bool,
    apply_changes: bool,
) -> int:
    """Plan required bootstrap actions or refresh a failed V2 controller."""
    target = ctx.registry.get(host_id)
    if target is None:
        raise entity_not_found("host", host_id)
    if plan_only and apply_changes:
        raise click.UsageError("pass at most one of --plan or --apply")
    address = _bootstrap_tailnet_address(target, ssh_host)
    if apply_changes and not bws_token_stdin:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host bootstrap apply requires a BWS token on standard input",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Pipe the host machine token to infralink host bootstrap --bws-token-stdin --apply",
            details={"host": target.uuid, "requirement": "bws_token_stdin"},
        )
    projects = _bootstrap_declared_bws_projects(ctx, target)
    token = _read_bootstrap_bws_token() if bws_token_stdin else None
    controller_state = _controller_bootstrap_state(ctx.registry_path, target)
    if token is not None:
        _validate_bootstrap_bws_access(
            ctx, projects, token, controller_secret=controller_state.registry_read_identity_secret
        )
    with _bootstrap_pinned_transport(ctx, target, address) as transport:
        probe = transport.probe(address)
        _require_remote_tailnet_identity(target, probe, address)
        readiness = _bootstrap_operator_readiness(
            evaluate_host_readiness(target, _BootstrapProbeTransport(probe), address=address)
        )
        if token is None:
            readiness = _readiness_with_bws_token_required(readiness)
        automated_actions = _bootstrap_executor_actions(readiness)
        if apply_changes and automated_actions:
            readiness = _apply_bootstrap_request(
                ctx,
                target,
                address,
                automated_actions,
                controller_state,
                token,
                transport.known_hosts,
            )
    actions = [
        action(
            "reinspect-readiness",
            [
                *_root_source_argv(ctx),
                "host",
                "bootstrap",
                target.uuid,
                "--ssh-host",
                address,
            ],
            "Reinspect live host readiness",
        )
    ]
    result = HostBootstrapPlanResult(
        host=DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name),
        readiness=readiness,
    )
    _emit(
        ok_envelope(
            _context_for(path=["host", "bootstrap"]),
            result,
            actions,
        )
    )
    return 0 if readiness.ready or plan_only else 1


_TAILNET_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _bootstrap_tailnet_address(target: Any, ssh_host: str) -> str:
    """Accept only the exact registry-owned Tailnet SSH target."""
    try:
        supplied = ipaddress.ip_address(ssh_host)
        declared = ipaddress.ip_address(str(target.tailscale_ip))
    except ValueError:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host bootstrap requires a declared Tailnet IPv4 address",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare the host Tailnet IPv4 and pass it with --ssh-host",
            details={"host": target.uuid},
        ) from None
    if (
        not isinstance(supplied, ipaddress.IPv4Address)
        or supplied not in _TAILNET_NETWORK
        or supplied != declared
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap SSH host must exactly match the declared Tailnet IPv4",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Pass the registry tailscale_ip with --ssh-host",
            details={"host": target.uuid, "declared_tailscale_ip": str(declared)},
        )
    return str(supplied)


def _bootstrap_declared_bws_projects(ctx: Context, target: Any) -> tuple[str, ...]:
    """Resolve the new explicit BWS access contract, without legacy fallbacks."""
    projects = tuple(getattr(target, "bws_projects", ()))
    machine_account = getattr(target, "bws_machine_account", None)
    if not projects or not isinstance(machine_account, str) or not machine_account.strip():
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host bootstrap requires bws_projects and bws_machine_account",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare the host machine account and one or more canonical bws_projects",
            details={"host": target.uuid},
        )
    if len(set(projects)) != len(projects) or any(
        not isinstance(item, str) or not item for item in projects
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host bootstrap bws_projects must be a unique nonempty list",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Correct the host bws_projects declaration",
            details={"host": target.uuid},
        )
    return projects


def _read_bootstrap_bws_token() -> str:
    token = sys.stdin.read().strip()
    if not token:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="BWS token standard input was empty",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Pipe the host machine token to host bootstrap --bws-token-stdin --apply",
            details={"requirement": "bws_token_stdin"},
        )
    return token


def _bws_project_catalog(ctx: Context) -> dict[str, str]:
    if ctx.registry_path is None:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap BWS validation requires a registry directory checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Use the checked-out registry hosts directory",
        )
    catalog = ctx.registry_path.parent / "ansible" / "inventory" / "bws_projects.yml"
    try:
        data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
        raw_projects = data["projects"]
        projects = {
            alias: entry["uuid"]
            for alias, entry in raw_projects.items()
            if isinstance(alias, str)
            and isinstance(entry, dict)
            and isinstance(entry.get("uuid"), str)
        }
    except (OSError, TypeError, KeyError, yaml.YAMLError):
        projects = {}
    if not projects:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap BWS project catalog is unavailable",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Provide a valid ansible/inventory/bws_projects.yml in the selected registry",
        )
    return projects


def _validate_bootstrap_bws_access(
    ctx: Context, aliases: tuple[str, ...], token: str, *, controller_secret: Any | None = None
) -> None:
    catalog = _bws_project_catalog(ctx)
    missing = [alias for alias in aliases if alias not in catalog]
    if missing:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Declared BWS project is absent from the registry catalog",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare only catalogued BWS project aliases",
            details={"projects": missing},
        )
    environment = {**os.environ, "BWS_ACCESS_TOKEN": token}
    for alias in aliases:
        completed = subprocess.run(
            ["bws", "project", "get", catalog[alias], "--output", "none"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
            env=environment,
        )
        if completed.returncode != 0:
            raise CliFailure(
                code=ErrorCode.PROVIDER_AUTHORIZATION_FAILED,
                message="BWS token cannot access a declared bootstrap project",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Grant the declared machine account access to every bws_projects entry",
                details={"project": alias},
            )
    if controller_secret is not None:
        expected_project = catalog.get(controller_secret.project)
        if expected_project is None:
            raise CliFailure(
                code=ErrorCode.CONFIGURATION_REQUIRED,
                message="Controller bootstrap secret project is absent from the registry catalog",
                exit_code=ExitCode.INPUT_ERROR,
                fix="Declare a catalogued project for controller_bootstrap.registry_read_identity_secret",
                details={"project": controller_secret.project},
            )
        completed = subprocess.run(
            ["bws", "secret", "get", controller_secret.id, "--output", "json"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
            env=environment,
        )
        try:
            secret = json.loads(completed.stdout) if completed.returncode == 0 else {}
            actual_project = secret.get("projectId")
        except json.JSONDecodeError:
            actual_project = None
        if actual_project != expected_project:
            raise CliFailure(
                code=ErrorCode.PROVIDER_AUTHORIZATION_FAILED,
                message="BWS token cannot read the declared controller registry secret",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Grant the host machine account access to the declared secret project",
                details={"project": controller_secret.project, "secret_id": controller_secret.id},
            )


class _BootstrapProbeTransport:
    """Reuse one pinned SSH observation without issuing a second connection."""

    def __init__(self, probe: HostReadinessProbe) -> None:
        self._probe = probe

    def probe(self, _address: str) -> HostReadinessProbe:
        return self._probe


@contextmanager
def _bootstrap_pinned_transport(
    ctx: Context, target: Any, address: str
) -> Iterator[SshReadinessTransport]:
    """Bootstrap uses the same declared SSH identity contract as reconcile."""
    from infralink.cli.operations import _pinned_known_hosts, resolve_apply_request

    if ctx.registry_path is None or not ctx.registry_path.is_dir():
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap requires a directory registry checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Use the selected registry hosts directory",
            details={"host": target.uuid},
        )
    request = resolve_apply_request(ctx.registry_path, target)
    if request.address != address:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap SSH address differs from the declared pinned host identity",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Correct the declared Tailnet address and SSH fingerprint",
            details={"host": target.uuid},
        )
    with _pinned_known_hosts(request) as known_hosts:
        yield SshReadinessTransport(known_hosts=known_hosts)


def _require_remote_tailnet_identity(target: Any, probe: HostReadinessProbe, address: str) -> None:
    # The SSH probe intentionally exposes only addresses, never Tailnet auth material.
    if not probe.reachable:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bootstrap SSH connection failed",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Install the authorized key and verify root SSH over the declared Tailnet address",
            details={"host": target.uuid, "ssh_host": address},
        )
    if address not in probe.tailscale_ips:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Remote host is not enrolled at its declared Tailnet address",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Enroll Tailscale manually and correct the host declaration before bootstrap",
            details={"host": target.uuid, "declared_tailscale_ip": address},
        )
    expected_name = target.tailscale_name or target.canonical_name
    if not probe.tailscale_running or probe.tailscale_name != expected_name:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Remote Tailscale identity does not match the declared host",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Start Tailscale and correct its host name before bootstrap",
            details={"host": target.uuid, "expected_name": expected_name},
        )


def _readiness_with_bws_token_required(readiness: HostReadinessResult) -> HostReadinessResult:
    return readiness.model_copy(
        update={
            "ready": False,
            "checks": [
                *readiness.checks,
                HostReadinessCheck(
                    id="bws_token",
                    required=True,
                    passed=False,
                    description="A host machine BWS token was supplied for bootstrap validation.",
                    detail="bws_token_required",
                ),
            ],
            "actions": [
                *readiness.actions,
                HostBootstrapAction(
                    id="provide_bws_token",
                    check_id="bws_token",
                    description="Rerun bootstrap with --bws-token-stdin and provide the host machine token on standard input.",
                ),
            ],
        }
    )


def _bootstrap_operator_readiness(readiness: HostReadinessResult) -> HostReadinessResult:
    """Show only actionable prerequisites plus the one controller action."""
    executable_prerequisites = {
        "establish_root_ssh",
        "correct_host_identity",
        "initialize_machine_id",
        "install_git",
        "install_docker",
        "install_tailscale",
        "install_jq",
        "install_bws_cli",
        "install_self_deploy_dependencies",
    }
    checks = [
        check
        for check in readiness.checks
        if check.id not in {"devops_account", "devops_authorized_access", "registry_layout"}
    ]
    actions = [item for item in readiness.actions if item.id in executable_prerequisites]
    if not readiness.ready:
        actions.append(
            HostBootstrapAction(
                id="bootstrap_infralink_controller",
                check_id="controller_bootstrap",
                description="Install the declared Infralink controller and reconcile timer.",
            )
        )
    return readiness.model_copy(
        update={
            "checks": checks,
            "actions": actions,
            "ready": all(not check.required or check.passed for check in checks),
        }
    )


def _bootstrap_executor_actions(readiness: HostReadinessResult) -> list[str]:
    """Translate only declared bootstrap prerequisites into executor actions."""
    executor_actions = {
        "install_git",
        "install_docker",
        "install_jq",
        "install_bws_cli",
        "install_self_deploy_dependencies",
    }
    actions = [item.id for item in readiness.actions if item.id in executor_actions]
    if not readiness.ready:
        actions.append("bootstrap_infralink_controller")
    return actions


def _bootstrap_execution_env(token: str | None) -> dict[str, str]:
    if token is None:
        return dict(os.environ)
    return {**os.environ, "BWS_ACCESS_TOKEN": token}


def _apply_bootstrap_request(
    ctx: Context,
    target: Any,
    address: str,
    actions: list[str],
    controller_state: HostControllerBootstrapState,
    token: str | None,
    known_hosts: Path | None,
) -> HostReadinessResult:
    """Run the sole baseline executor with the probe's pinned host identity."""
    if known_hosts is None:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap requires a pinned SSH host key",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare ssh.host_key_fingerprint before bootstrap",
            details={"host": target.uuid},
        )
    request = _bootstrap_apply_request(
        ctx, target, actions, address=address, controller_state=controller_state
    )
    with _bootstrap_executor_source(_CONTROL_ROOT, actions) as (source, playbook):
        completed = subprocess.run(
            [
                "ansible-playbook",
                "-i",
                f"{request.host_address},",
                "-u",
                "root",
                "--ssh-common-args",
                f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts}",
                str(playbook),
                "-e",
                json.dumps(request.ansible_extra_vars(), sort_keys=True),
            ],
            cwd=source,
            text=True,
            capture_output=True,
            check=False,
            env=_bootstrap_execution_env(token),
        )
    if completed.returncode != 0:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Host baseline apply failed",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify the declared bootstrap executor and rerun host bootstrap --apply",
            details=_bootstrap_failure_details(target.uuid, completed),
        )
    return _bootstrap_operator_readiness(
        evaluate_host_readiness(
            target, SshReadinessTransport(known_hosts=known_hosts), address=address
        )
    )


@contextmanager
def _bootstrap_executor_source(
    control_root: Path, actions: Sequence[str]
) -> Iterator[tuple[Path, Path]]:
    """Use a clean detached snapshot of the authoritative management main branch."""
    manifest_path = "ansible/executors/infralink-host-baseline.json"
    with _controller_refresh_source(
        control_root,
        None,
        required_path=manifest_path,
        capability="host_bootstrap",
    ) as source:
        try:
            manifest = json.loads((source / manifest_path).read_text(encoding="utf-8"))
            playbook_path = manifest["playbook"]
            allowed_actions = manifest["allowed_actions"]
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            manifest = None
            playbook_path = None
            allowed_actions = None
        if (
            not isinstance(manifest, dict)
            or manifest.get("schema_version") != "infralink.host-bootstrap-executor/v1"
            or manifest.get("id") != "infra-management-host-baseline"
            or playbook_path != "ansible/playbooks/infralink_host_baseline.yml"
            or not isinstance(allowed_actions, list)
            or not all(isinstance(action_id, str) for action_id in allowed_actions)
            or not set(actions).issubset(allowed_actions)
        ):
            raise CliFailure(
                code=ErrorCode.CONFIGURATION_REQUIRED,
                message="Selected host bootstrap executor does not support the requested actions",
                exit_code=ExitCode.INPUT_ERROR,
                fix="Publish a valid immutable infralink host-bootstrap executor",
                details={"capability": "host_bootstrap"},
            )
        playbook = source / playbook_path
        if not playbook.is_file():
            raise CliFailure(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Selected host bootstrap executor playbook is unavailable",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Publish the declared executor playbook at the selected revision",
                details={"capability": "host_bootstrap"},
            )
        yield source, playbook


def _bootstrap_apply_request(
    ctx: Context,
    target: Any,
    automated_actions: list[str],
    *,
    address: str | None = None,
    controller_state: HostControllerBootstrapState | None = None,
) -> HostBootstrapRequest:
    """Resolve a bounded executor request before any remote mutation begins."""
    address = address or target.tailscale_ip
    if not address:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host address is required for bootstrap",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare a Tailnet address for the host",
            details={"host": target.uuid},
        )
    try:
        controller_bootstrap: HostControllerBootstrapState | None = controller_state
        return HostBootstrapRequest.model_validate(
            {
                "host_address": str(address),
                "host_uuid": target.uuid,
                "canonical_name": target.canonical_name,
                "bootstrap_actions": automated_actions,
                "controller_bootstrap": controller_bootstrap,
            }
        )
    except CliFailure:
        raise
    except ValueError:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host declaration is incomplete for bootstrap",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare complete V2 bootstrap state and rerun host bootstrap --plan",
            details={"host": target.uuid},
        ) from None


def _controller_bootstrap_state(
    registry_path: Path | None, target: Any
) -> HostControllerBootstrapState:
    if registry_path is None:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Controller bootstrap requires a registry directory checkout",
            exit_code=ExitCode.INPUT_ERROR,
        )
    manifest_path = registry_path / target.uuid / "manifest.yml"
    deployment_path = registry_path / target.uuid / "operations" / "deployment.yml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["hosts"][target.uuid]
        bootstrap = manifest["controller_bootstrap"]
        deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
        image = deployment["controller"]["image"]
        state = HostControllerBootstrapState.model_validate(
            {
                "controller_image": _controller_image_reference(image),
                "registry_read_identity_secret": bootstrap["registry_read_identity_secret"],
                "registry_repo_url": bootstrap["registry_repo_url"],
                "registry_ref": bootstrap["registry_ref"],
            }
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host declaration lacks canonical controller bootstrap state",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare controller_bootstrap with registry key reference, repository, and ref",
            details={"host": target.uuid},
        ) from None
    return state


def _controller_image_reference(image: Any) -> str:
    """Use the same head/branch selector semantics for bootstrap and reconcile."""
    if not isinstance(image, dict):
        raise ValueError("controller image must be a mapping")
    repository = image.get("repository")
    tag = image.get("tag")
    if tag == "head":
        tag = image.get("branch", "main")
    if not isinstance(repository, str) or not repository or not isinstance(tag, str) or not tag:
        raise ValueError("controller image reference is incomplete")
    return f"{repository}:{tag}"


def _bootstrap_failure_details(
    host_uuid: str, completed: subprocess.CompletedProcess[str]
) -> dict[str, Any]:
    """Expose bounded execution shape only, never controller-rendered task names."""
    task_count = min(len(re.findall(r"^TASK \[[^\]]+\]", completed.stdout, re.MULTILINE)), 8)
    details: dict[str, Any] = {
        "host": host_uuid,
        "executor": "host_baseline",
        "return_code": completed.returncode,
    }
    if task_count:
        details["task_count"] = task_count
        details["task_output_redacted"] = True
    return details


def _apply_controller_refresh(ctx: Context, target: Any, runtime_revision: str | None) -> None:
    """Run only the pinned controller refresh playbook over declared SSH."""
    from infralink.cli.operations import _pinned_known_hosts, resolve_apply_request

    if ctx.registry_path is None or not ctx.registry_path.is_dir():
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Controller refresh requires a directory registry checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Provide the selected registry hosts directory",
            details={"host": target.uuid},
        )
    request = resolve_apply_request(ctx.registry_path, target)
    resolved_runtime_revision, extra_vars = _controller_refresh_extra_vars(
        ctx.registry_path, target
    )
    if runtime_revision is not None and runtime_revision != resolved_runtime_revision:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected controller runtime revision changed during bootstrap",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Re-run host bootstrap --plan and apply the newly selected controller revision",
            details={"host": target.uuid},
        )
    runtime_revision = resolved_runtime_revision
    control_root = _CONTROL_ROOT
    ssh_args = "-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes "
    try:
        with _controller_refresh_source(control_root, runtime_revision) as source:
            with _pinned_known_hosts(request) as known_hosts:
                completed = subprocess.run(
                    [
                        "ansible-playbook",
                        "-i",
                        f"{request.address},",
                        "-u",
                        "root",
                        "--ssh-common-args",
                        ssh_args + f"-o UserKnownHostsFile={known_hosts}",
                        str(source / _CONTROLLER_REFRESH_PLAYBOOK),
                        "-e",
                        json.dumps(extra_vars, sort_keys=True),
                    ],
                    cwd=source,
                    text=True,
                    capture_output=True,
                    check=False,
                )
    except (OSError, subprocess.TimeoutExpired):
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Declared host controller refresh is unavailable",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify the declared SSH transport and rerun host bootstrap --apply",
            details={"host": target.uuid, "runtime_revision": runtime_revision},
        ) from None
    if completed.returncode != 0:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Host controller refresh failed",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Inspect Bastion Ansible logs and rerun host bootstrap --apply",
            details={"host": target.uuid, "runtime_revision": runtime_revision},
        )


@contextmanager
def _controller_refresh_source(
    control_root: Path,
    revision: str | None,
    *,
    required_path: str | None = None,
    capability: str = "controller_refresh",
) -> Iterator[Path]:
    """Materialize an immutable management tree, never the live checkout."""
    required_path = required_path or _CONTROLLER_REFRESH_PLAYBOOK
    status = subprocess.run(
        ["git", "-C", str(control_root), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if status.returncode != 0 or status.stdout:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot materialize the selected immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Clean and refresh the Bastion infra-management checkout at the selected revision",
            details={"capability": capability, "required_revision": revision},
        )
    remote = subprocess.run(
        ["git", "-C", str(control_root), "remote", "get-url", "origin"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if remote.returncode != 0 or _controller_remote_identity(
        remote.stdout.strip()
    ) != _controller_remote_identity(_CONTROLLER_REFRESH_SOURCE_REMOTE):
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot fetch the selected immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Configure the expected infra-management origin and rerun host bootstrap --apply",
            details={"capability": capability, "required_revision": revision},
        )
    # `main` is transport only: the candidate-selected revision remains the
    # sole executable identity and must be reachable from the expected remote.
    fetched = subprocess.run(
        [
            "git",
            "-C",
            str(control_root),
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if revision is None and fetched.returncode == 0:
        resolved = subprocess.run(
            ["git", "-C", str(control_root), "rev-parse", "origin/main"],
            text=True,
            capture_output=True,
            check=False,
            env=_isolated_git_environment(),
        )
        revision = resolved.stdout.strip() if resolved.returncode == 0 else ""
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot resolve the immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify Bastion can read the expected infra-management origin and rerun host bootstrap --apply",
            details={"capability": capability},
        )
    present = subprocess.run(
        ["git", "-C", str(control_root), "cat-file", "-e", f"{revision}^{{commit}}"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    selected_from_main = subprocess.run(
        ["git", "-C", str(control_root), "merge-base", "--is-ancestor", revision, "origin/main"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if fetched.returncode != 0 or present.returncode != 0 or selected_from_main.returncode != 0:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot fetch the selected immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify Bastion can read the expected infra-management origin and rerun host bootstrap --apply",
            details={"capability": capability, "required_revision": revision},
        )
    with tempfile.TemporaryDirectory(prefix="infralink-controller-refresh-") as temporary:
        source = Path(temporary) / "source"
        created = subprocess.run(
            [
                "git",
                "-C",
                str(control_root),
                "worktree",
                "add",
                "--detach",
                str(source),
                revision,
            ],
            text=True,
            capture_output=True,
            check=False,
            env=_isolated_git_environment(),
        )
        head = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
            env=_isolated_git_environment(),
        )
        required = source / required_path
        if (
            created.returncode != 0
            or head.returncode != 0
            or head.stdout.strip() != revision
            or not required.is_file()
        ):
            raise CliFailure(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Bastion could not materialize the selected immutable management source",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Refresh the Bastion infra-management clone with the selected controller revision",
                details={"capability": capability, "required_revision": revision},
            )
        completed = False
        try:
            yield source
            completed = True
        finally:
            removed = subprocess.run(
                ["git", "-C", str(control_root), "worktree", "remove", "--force", str(source)],
                text=True,
                capture_output=True,
                check=False,
                env=_isolated_git_environment(),
            )
            if completed and removed.returncode != 0:
                raise CliFailure(
                    code=ErrorCode.ARTIFACT_IO_FAILED,
                    message="Bastion could not remove the temporary management source",
                    exit_code=ExitCode.ARTIFACT_IO_ERROR,
                    fix="Remove the temporary controller worktree and rerun host bootstrap --apply",
                    details={"capability": capability, "required_revision": revision},
                )


def _controller_refresh_extra_vars(registry_path: Path, target: Any) -> tuple[str, dict[str, Any]]:
    """Read the host controller revision, falling back to the fleet lock."""
    manifest_path = registry_path / target.uuid / "manifest.yml"
    try:
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        declaration = document["hosts"][target.uuid]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        declaration = None
    deployment_path = registry_path / target.uuid / "operations" / "deployment.yml"
    if os.path.lexists(deployment_path):
        if deployment_path.is_file():
            try:
                deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
                runtime_revision = deployment["infra_management"]["revision"]
            except (OSError, KeyError, TypeError, yaml.YAMLError):
                runtime_revision = ""
        else:
            runtime_revision = ""
        revision_source = deployment_path
    else:
        lock = registry_path.parent / "operations" / "infra-management.lock"
        try:
            runtime_revision = lock.read_text(encoding="utf-8").strip()
        except OSError:
            runtime_revision = ""
        revision_source = lock
    if (
        not isinstance(runtime_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", runtime_revision) is None
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host does not bind an exact controller runtime revision",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare infra_management.revision in the host deployment or publish a valid fleet fallback lock",
            details={"path": str(revision_source)},
        )
    required_true = (
        "self_deploy_v2_reconcile_enabled",
        "self_deploy_v2_reconcile_packaged",
        "self_deploy_v2_promotion_policy_enabled",
    )
    required_strings = (
        "self_deploy_v2_promotion_registry_remote",
        "self_deploy_v2_promotion_bws_project_id",
        "self_deploy_v2_registry_read_identity_secret_uuid",
        "self_deploy_v2_promotion_host_fingerprint",
        "self_deploy_v2_promotion_allowed_signers",
        "self_deploy_v2_promotion_channel",
        "self_deploy_registry_origin",
    )
    if (
        not isinstance(declaration, dict)
        or any(declaration.get(name) is not True for name in required_true)
        or declaration.get("self_deploy_legacy_cron_enabled") is not False
        or any(
            not isinstance(declaration.get(name), str) or not declaration[name]
            for name in required_strings
        )
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host declaration is incomplete for controller refresh",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare the complete V2 controller inputs in the selected host manifest",
            details={"host": target.uuid},
        )
    return runtime_revision, {
        "uuid": target.uuid,
        "canonical_name": target.canonical_name,
        "self_deploy_v2_runtime_revision": runtime_revision,
        "self_deploy_v2_reconcile_enabled": True,
        "self_deploy_v2_reconcile_packaged": True,
        "self_deploy_v2_promotion_policy_enabled": True,
        "self_deploy_legacy_cron_enabled": False,
        **{name: declaration[name] for name in required_strings},
    }


@host.command(name="verifier")
@click.argument("host_ref")
@pass_context
def host_verifier(ctx: Context, host_ref: str) -> int:
    """Inspect the declared host's public V2 Git signature verifier facts."""
    from infralink.cli.operations import inspect_verifier, resolve_apply_request

    target = ctx.registry.get(host_ref)
    if target is None:
        raise entity_not_found("host", host_ref)
    if ctx.registry_path is None:
        raise configuration_required("registry")
    verifier = inspect_verifier(resolve_apply_request(ctx.registry_path, target))
    doctor_target = DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name)
    _emit(
        ok_envelope(
            _context_for(path=["host", "verifier"]),
            HostVerifierResult(target=doctor_target, verifier=verifier),
            [
                action(
                    "doctor",
                    [*_root_source_argv(ctx), "doctor", "host", target.uuid],
                    "Reinspect the host convergence result",
                )
            ],
        )
    )
    return 0 if verifier.signature_verification == "passed" and not verifier.unavailable else 1


@host.command(name="apply")
@click.argument("host_ref")
@click.option("--dry-run", is_flag=True, help="Validate host apply inputs without submitting work.")
@click.option("--wait", "wait", is_flag=True, help="Wait for a terminal host apply result.")
@click.option(
    "--timeout",
    type=click.IntRange(min=1, max=3600),
    default=300,
    show_default=True,
    help="Maximum seconds to wait when --wait is set.",
)
@pass_context
def host_apply(ctx: Context, host_ref: str, dry_run: bool, wait: bool, timeout: int) -> int:
    """Start the declared host-local reconcile unit through SSH."""
    from infralink.cli.operations import (
        operation_provider,
        resolve_apply_request,
        validate_target_ssh_identity,
        wait_for_terminal,
    )

    target = ctx.registry.get(host_ref)
    if target is None:
        raise entity_not_found("host", host_ref)
    if ctx.registry_path is None:
        raise configuration_required("registry")
    request = resolve_apply_request(ctx.registry_path, target)
    doctor_target = DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name)
    if dry_run:
        completed = subprocess.run(
            ["git", "-C", str(ctx.registry_path.parent), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
        )
        revision = completed.stdout.strip()
        if completed.returncode != 0 or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
            raise CliFailure(
                code=ErrorCode.INPUT_LOAD_FAILED,
                message="Selected registry revision could not be resolved",
                exit_code=ExitCode.INPUT_ERROR,
                fix="Use a Git checkout containing the selected registry revision",
                details={"registry": str(ctx.registry_path)},
            )
        validate_target_ssh_identity(request)
        _emit(
            ok_envelope(
                _context_for(path=["host", "apply"]),
                HostApplyResult(
                    target=doctor_target,
                    dry_run=True,
                    plan=HostApplyPlan(
                        registry_revision=revision,
                        dispatch_provider="ssh",
                        reconcile_mode="timer",
                        action_categories=["registry_checkout", "render", "reconcile"],
                    ),
                    ssh_host_identity="passed",
                ),
                [
                    action(
                        "apply",
                        [*_root_source_argv(ctx), "host", "apply", target.uuid],
                        "Submit this host apply",
                        safe=False,
                    )
                ],
            )
        )
        return 0

    provider = operation_provider()
    try:
        record = provider.submit(request)
    except CliFailure as failure:
        dispatch_status = failure.details.get("dispatch")
        if failure.code != ErrorCode.PROVIDER_UNAVAILABLE or dispatch_status not in {
            "rejected",
            "unavailable",
        }:
            raise
        from infralink.cli.operations import inspect_target_status

        target_status = _target_reconcile_status(inspect_target_status(request))
        result = HostApplyResult(
            target=doctor_target,
            dispatch=HostDispatch(
                provider="ssh",
                status=cast(Literal["rejected", "unavailable"], dispatch_status),
            ),
            target_status=target_status,
        )
        actions = [
            action(
                "status",
                [*_root_source_argv(ctx), "host", "status", target.uuid],
                "Inspect the target timer and latest reconcile result",
            ),
            action(
                "logs",
                [*_root_source_argv(ctx), "host", "logs", target.uuid, "--last-run"],
                "Inspect bounded evidence from the target's latest reconcile run",
            ),
        ]
        _emit(ok_envelope(_context_for(path=["host", "apply"]), result, actions))
        return 0 if target_status.last_reconcile.status == "success" else 1
    if wait:
        record = wait_for_terminal(provider, record.id, request, timeout_seconds=timeout)
    result = HostApplyResult(
        operation=OperationSummary(
            id=record.id,
            state=cast(Literal["queued", "applying", "converged", "failed"], record.state),
        ),
        target=doctor_target,
        dispatch=HostDispatch(provider="ssh", status="accepted"),
        ssh_host_identity="passed",
        failure=record.failure,
    )
    actions = []
    if record.state in {"queued", "applying"}:
        actions.append(
            action(
                "status",
                [*_root_action_prefix(ctx), "operation", "status", record.id],
                "Check host apply progress",
            )
        )
    else:
        actions.append(
            action(
                "doctor",
                [*_root_source_argv(ctx), "doctor", "host", target.uuid],
                "Inspect the host convergence result",
            )
        )
    _emit(ok_envelope(_context_for(path=["host", "apply"]), result, actions))
    return 0 if record.state == "converged" or not wait else 1


def _target_reconcile_status(values: dict[str, str]) -> TargetReconcileStatus:
    result_value = values.get("unit_result")
    status = "success" if result_value == "success" else "failed" if result_value else "unknown"
    sha = values.get("registry_sha")
    active = values.get("unit_active") in {"active", "activating", "reloading"}
    return TargetReconcileStatus(
        reconcile_mode="timer",
        timer=HostTimer(
            active=values.get("timer_active") == "active",
            next_scheduled_at=values.get("timer_next") or None,
        ),
        in_progress=active,
        last_reconcile=LastReconcile(
            status=cast(Literal["success", "failed", "unknown"], status),
            registry_sha=sha if re.fullmatch(r"[0-9a-f]{40}", sha or "") else None,
            finished_at=(
                values["finished_at"]
                if re.fullmatch(
                    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z",
                    values.get("finished_at", ""),
                )
                else None
            ),
        ),
    )


@host.command(name="status")
@click.argument("host_ref")
@pass_context
def host_status(ctx: Context, host_ref: str) -> int:
    """Read the declared host's timer and latest reconcile evidence."""
    from infralink.cli.operations import inspect_target_status, resolve_apply_request

    target = ctx.registry.get(host_ref)
    if target is None:
        raise entity_not_found("host", host_ref)
    if ctx.registry_path is None:
        raise configuration_required("registry")
    values = inspect_target_status(resolve_apply_request(ctx.registry_path, target))
    target_status = _target_reconcile_status(values)
    _emit(
        ok_envelope(
            _context_for(path=["host", "status"]),
            HostStatusResult(
                target=DoctorTarget(
                    type="host", id=target.uuid, canonical_name=target.canonical_name
                ),
                **target_status.model_dump(),
            ),
            [
                action(
                    "logs",
                    [*_root_source_argv(ctx), "host", "logs", target.uuid, "--last-run"],
                    "Inspect bounded evidence from the target's latest reconcile run",
                )
            ],
        )
    )
    return 0 if target_status.last_reconcile.status == "success" else 1


@host.command(name="logs")
@click.argument("host_ref")
@click.option(
    "--last-run",
    is_flag=True,
    required=True,
    help="Show bounded evidence from the latest reconcile run.",
)
@pass_context
def host_logs(ctx: Context, host_ref: str, last_run: bool) -> int:
    """Read bounded sanitized evidence from the declared host's latest reconcile run."""
    from infralink.cli.operations import inspect_target_logs, resolve_apply_request

    target = ctx.registry.get(host_ref)
    if target is None:
        raise entity_not_found("host", host_ref)
    if ctx.registry_path is None:
        raise configuration_required("registry")
    _emit(
        ok_envelope(
            _context_for(path=["host", "logs"]),
            HostLogsResult(
                target=DoctorTarget(
                    type="host", id=target.uuid, canonical_name=target.canonical_name
                ),
                lines=inspect_target_logs(resolve_apply_request(ctx.registry_path, target)),
            ),
            [
                action(
                    "status",
                    [*_root_source_argv(ctx), "host", "status", target.uuid],
                    "Inspect target reconcile status",
                )
            ],
        )
    )
    return 0


@click.group()
def operation() -> None:
    """Inspect durable host apply operations."""


@operation.command(name="status")
@click.argument("operation_id")
@pass_context
def operation_status(ctx: Context, operation_id: str) -> int:
    """Get the current state of one declared host-local reconcile operation."""
    from infralink.cli.operations import operation_provider, resolve_apply_request

    if operation_id.startswith("op_"):
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Legacy control-plane operation status is unavailable",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Start a new declared host-local apply operation",
            details={"operation_id": operation_id},
        )
    parts = operation_id.split("/", 2)
    host_ref = parts[1] if len(parts) == 3 and parts[0] == "ssh" else operation_id
    target_host = ctx.registry.get(host_ref)
    if target_host is None:
        raise entity_not_found("host", host_ref)
    if ctx.registry_path is None:
        raise configuration_required("registry")
    record = operation_provider().status(
        operation_id, resolve_apply_request(ctx.registry_path, target_host)
    )
    target = DoctorTarget.model_validate(record.target) if record.target is not None else None
    result = OperationStatusResult(
        operation=OperationSummary(
            id=record.id,
            state=cast(Literal["queued", "applying", "converged", "failed"], record.state),
        ),
        target=target,
        failure=record.failure,
    )
    actions = []
    if record.state in {"queued", "applying"}:
        actions.append(
            action(
                "status",
                [*_root_action_prefix(ctx), "operation", "status", record.id],
                "Check host apply progress",
            )
        )
    _emit(ok_envelope(_context_for(path=["operation", "status"]), result, actions))
    return 0 if record.state != "failed" else 1


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
