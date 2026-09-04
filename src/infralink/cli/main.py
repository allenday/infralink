"""Main CLI entry point for infralink."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from collections.abc import Mapping, Sequence
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NoReturn

import click
import yaml
from agent_surface import OperationError

from infralink import __version__
from infralink.cli import command_plugins
from infralink.cli.actions import action, redact_argv
from infralink.cli.contracts import (
    Action,
    ArgumentDescriptor,
    Binding,
    CommandContext,
    CommandDescriptor,
    HelpNavigationAction,
    HelpResult,
    HelpSubcommand,
    OptionDescriptor,
    RootResult,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode, internal_failure
from infralink.cli.output import (
    command_context,
    error_envelope,
    ok_envelope,
)
from infralink.operator_config import (
    OperatorConfigError,
)
from infralink.operator_config import (
    configured_registry as _configured_registry_value,
)
from infralink.operator_operations.host_bootstrap import (
    _apply_bootstrap_request,
    _bootstrap_apply_request,
    _bootstrap_declared_bws_projects,
    _bootstrap_executor_actions,
    _bootstrap_executor_source,
    _bootstrap_failure_details,
    _bootstrap_operator_readiness,
    _bootstrap_pinned_transport,
    _bootstrap_plan_actions,
    _bootstrap_tailnet_address,
    _BootstrapProbeTransport,
    _controller_bootstrap_state,
    _read_bootstrap_bws_token,
    _readiness_with_bws_token_required,
    _require_remote_tailnet_identity,
    _validate_bootstrap_bws_access,
)
from infralink.operator_sources import resolve_registry_companion
from infralink.operator_surface import operator_click_adapter

# Transitional re-exports preserve the existing test and extension import path
# while bootstrap ownership moves behind the transport-neutral operation module.
__all__ = [
    "_apply_bootstrap_request",
    "_bootstrap_apply_request",
    "_bootstrap_declared_bws_projects",
    "_bootstrap_executor_actions",
    "_bootstrap_executor_source",
    "_bootstrap_failure_details",
    "_bootstrap_operator_readiness",
    "_bootstrap_pinned_transport",
    "_bootstrap_plan_actions",
    "_bootstrap_tailnet_address",
    "_BootstrapProbeTransport",
    "_controller_bootstrap_state",
    "_read_bootstrap_bws_token",
    "_readiness_with_bws_token_required",
    "_require_remote_tailnet_identity",
    "_validate_bootstrap_bws_access",
]

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
_ALLOW_EXTERNAL_COMMANDS: ContextVar[bool] = ContextVar(
    "infralink_allow_external_commands", default=True
)


def current_invocation_argv() -> tuple[str, ...]:
    """Return the active root invocation for mounted typed command projections."""
    return tuple(_INVOCATION_ARGS.get() or ())


def _configured_registry() -> Path | None:
    """Read a local checkout selector; it is not desired-state input."""
    try:
        return _configured_registry_value()
    except OperatorConfigError as error:
        raise input_load_failed("operator config", str(error)) from None


def registry_checkout_root(path: Path | None) -> Path | None:
    """Return the configured checkout root without discarding catalog material."""
    if path is None or not path.is_dir():
        return None
    if (path / "hosts").is_dir():
        return path
    if path.name == "hosts" and path.parent.is_dir() and (path.parent / "hosts").is_dir():
        return path.parent
    return None


def registry_companion(path: Path | None, relative: str) -> Path | None:
    root = registry_checkout_root(path)
    candidate = root / relative if root is not None else None
    return candidate if candidate is not None and candidate.exists() else None


class Context:
    """CLI context object passed to commands."""

    def __init__(self) -> None:
        self.registry_path: Path | None = None
        self._hosts_path: Path | None = None
        self.edges_path: Path | None = None
        self.verbose: bool = False
        self.output: str = "yaml"
        self.output_explicit: bool = False
        self._registry: Any = None
        self._edges: Any = None

    @property
    def hosts_path(self) -> Path | None:
        if self._hosts_path is not None:
            return self._hosts_path
        if self.registry_path is None:
            return None
        nested_hosts = self.registry_path / "hosts"
        return nested_hosts if nested_hosts.is_dir() else self.registry_path

    @hosts_path.setter
    def hosts_path(self, value: Path | None) -> None:
        self._hosts_path = value

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
                    self._registry = Registry.load_dir(self.hosts_path or self.registry_path)
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
        "usage": "infralink --registry <checkout-root> analyze --output <directory>",
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
    "fleet": {
        "description": "Validate declared fleet topology without host-side operations.",
        "usage": "infralink fleet validate",
    },
    "app": {"description": "Manage applications.", "usage": "infralink app [list|show]"},
    "info": {"description": "Show registry and edge summary.", "usage": "infralink info"},
    "mcp": {
        "description": "Serve typed Infralink tools over MCP stdio.",
        "usage": "infralink mcp serve",
    },
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
    "registry": {
        "description": "Inspect and author local registry declarations.",
        "usage": "infralink registry host [get|patch] <ref>",
    },
    "controller": {
        "description": "Run typed controller runtime operations.",
        "usage": "infralink controller [doctor|reconcile|bootstrap]",
    },
}


def _command_descriptor(name: str) -> CommandDescriptor:
    """Describe built-ins and manifest-backed commands in the root contract."""
    metadata = COMMAND_METADATA.get(name)
    if metadata is not None:
        return CommandDescriptor(
            name=name,
            description=metadata.get("description", ""),
            usage=metadata.get("usage", f"infralink {name}"),
        )
    return CommandDescriptor(
        name=name,
        description=command_plugins.root_summary(name),
        usage=f"infralink {name}",
    )


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
        "description": "Read target reconcile evidence, or an explicit private diagnostic for the latest run.",
        "arguments": [{"name": "host_ref", "type": "string", "required": True}],
        "options": [
            {"name": "last_run", "type": "boolean", "required": True},
            {"name": "diagnostic", "type": "boolean", "required": False},
        ],
        "examples": ["infralink host logs relaxgg-db-es1 --last-run"],
    },
    ("registry",): {
        "description": "Inspect and author local registry declarations.",
        "arguments": [],
        "options": [],
        "examples": ["infralink registry host get relaxgg-db-es1"],
    },
    ("registry", "host"): {
        "description": "Resolve and patch host declarations in an operator working tree.",
        "arguments": [],
        "options": [],
        "examples": ["infralink registry host get relaxgg-db-es1"],
    },
    ("registry", "host", "get"): {
        "description": "Resolve a host to its authoritative manifest and declaration.",
        "arguments": [{"name": "host_ref", "type": "string", "required": True}],
        "options": [],
        "examples": ["infralink registry host get relaxgg-db-es1"],
    },
    ("registry", "host", "patch"): {
        "description": "Preview or explicitly write typed dot-addressed host mutations.",
        "arguments": [{"name": "host_ref", "type": "string", "required": True}],
        "options": [
            {"name": "set", "type": "text", "required": True},
            {"name": "write", "type": "boolean", "required": False},
        ],
        "examples": [
            "infralink registry host patch HOST --set controller_bootstrap.bootstrap_note=@text:FILE",
            "infralink registry host patch HOST --set controller_bootstrap.pull_enabled=@yaml:FILE --write",
        ],
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
    *,
    allow_external_commands: bool = True,
    ignore_root_sources: bool = False,
) -> CommandContext:
    active_argv = argv
    if active_argv is None:
        active_argv = _INVOCATION_ARGS.get() or []
    redacted_argv = redact_argv(active_argv)
    parsed_path, parsed_args, root_values = _parse_invocation(
        redacted_argv, allow_external_commands=allow_external_commands
    )
    click_ctx = click.get_current_context(silent=True)
    runtime_ctx = click_ctx.find_root().obj if click_ctx is not None else None
    effective_registry = root_values.get("registry")
    effective_edges = root_values.get("edges")
    if isinstance(runtime_ctx, Context):
        effective_registry = runtime_ctx.registry_path or effective_registry
        effective_edges = runtime_ctx.edges_path or effective_edges
    if ignore_root_sources:
        root_values["registry"] = None
        root_values["edges"] = None
        effective_registry = None
        effective_edges = None
    resolved = {
        "version": __version__,
        "cwd": os.getcwd(),
        "registry": _source_value(effective_registry),
        "edges": _source_value(effective_edges),
        "output": root_values.get("output", "yaml"),
        "verbose": bool(root_values.get("verbose", False)),
    }
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


def registry_checkout_required(path: str) -> CliFailure:
    """Require an explicit registry repository root for checkout-backed commands."""
    return CliFailure(
        code=ErrorCode.INPUT_LOAD_FAILED,
        message="Registry source must be the checkout root",
        exit_code=3,
        fix="Pass the repository root, not a registry YAML file or its hosts subdirectory.",
        details={"source": "registry", "path": path, "reason": "checkout_root_required"},
        next_actions=[
            action(
                "help",
                [*_action_argv_prefix(), "help", "analyze"],
                "Show analyze input options",
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


def _raise_cli_operation_error(error: Exception) -> NoReturn:
    """Translate the typed operation boundary back to the legacy CLI envelope."""
    from agent_surface import OperationError

    if not isinstance(error, OperationError):
        raise error
    details = dict(error.details[0]) if len(error.details) == 1 else {"items": list(error.details)}
    try:
        code = ErrorCode(error.code)
    except ValueError:
        code = (
            ErrorCode.INPUT_LOAD_FAILED
            if error.code in {"source_not_found", "source_invalid"}
            else ErrorCode.INTERNAL_ERROR
        )
    raise CliFailure(
        code=code,
        message=error.message,
        exit_code=(
            ExitCode.PROVIDER_ERROR
            if code.value.startswith("provider_")
            else ExitCode.INTERNAL_ERROR
            if code is ErrorCode.INTERNAL_ERROR
            else ExitCode.USAGE_ERROR
            if code in {ErrorCode.USAGE_ERROR, ErrorCode.INVALID_CURSOR}
            else ExitCode.INPUT_ERROR
        ),
        fix=error.fix or "Correct the declared host operation and retry",
        details=details,
    ) from None


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
    *,
    allow_external_commands: bool = True,
) -> tuple[list[str], dict[str, Any], dict[str, Any]]:
    external_commands_token = _ALLOW_EXTERNAL_COMMANDS.set(allow_external_commands)
    try:
        root_ctx = cli.make_context("infralink", list(argv), resilient_parsing=True)
    finally:
        _ALLOW_EXTERNAL_COMMANDS.reset(external_commands_token)
    root_values = dict(root_ctx.params)
    protected = _protected_args(root_ctx)
    if not protected:
        return [], {}, root_values

    name = protected[0]
    path = [name]
    command = _load_command_with_policy(name, allow_external_commands=allow_external_commands)
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

    path, _, _ = _parse_invocation(
        redact_argv(argv[: argv.index("--help")]), allow_external_commands=False
    )
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
    incoming = _INVOCATION_ARGS.get()
    # Error handlers run after Click has unwound the inner command context. During
    # MCP serving, the remaining ambient context belongs to `mcp serve` and defaults
    # to YAML, while the active tool invocation explicitly selected JSON.
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
    command = _command_for_path(path, allow_external_commands=False)
    if command is None:
        return _manifest_help_result(path)
    arguments, options = _help_parameters(command)
    return HelpResult(
        path=list(path),
        description=_command_description(command),
        arguments=arguments,
        options=options,
        examples=list(HELP_METADATA.get(path, {}).get("examples", [])),
        children=_help_children(path, command),
    )


def _manifest_help_result(path: tuple[str, ...]) -> HelpResult:
    """Render help from an installed Agent Surface manifest without importing it."""
    operation = command_plugins.operation(path)
    children = command_plugins.children(path)
    if operation is None and not children:
        raise click.UsageError("Unknown command path")
    if operation is None:
        return HelpResult(
            path=list(path),
            description=command_plugins.root_summary(path[0]),
            arguments=[],
            options=[],
            examples=[],
            children=_manifest_help_children(path),
        )
    input_schema = operation.operation["input_schema"]
    assert isinstance(input_schema, Mapping)
    properties = input_schema.get("properties", {})
    required = input_schema.get("required", [])
    if not isinstance(properties, Mapping) or not isinstance(required, list):
        raise RuntimeError("command_plugin_manifest_invalid")
    return HelpResult(
        path=list(path),
        description=operation.summary,
        arguments=[],
        options=[
            OptionDescriptor(
                name=name,
                type=_manifest_schema_type(schema),
                required=name in required,
            )
            for name, schema in sorted(properties.items())
            if isinstance(name, str) and name not in {"registry", "edges"}
        ],
        examples=[],
        children=_manifest_help_children(path),
    )


def _manifest_schema_type(schema: Any) -> str:
    if not isinstance(schema, Mapping):
        return "string"
    value = schema.get("type")
    return value if isinstance(value, str) else "string"


def _manifest_help_children(path: tuple[str, ...]) -> list[HelpSubcommand]:
    children: list[HelpSubcommand] = []
    for operation in command_plugins.children(path):
        name = operation.path[-1]
        argv = [*_help_argv_prefix(), *path, name]
        children.append(
            HelpSubcommand(
                name=name,
                summary=operation.summary,
                action=HelpNavigationAction(command=" ".join(argv), argv=argv),
            )
        )
    return children


def _help_parameters(
    command: click.Command,
) -> tuple[list[ArgumentDescriptor], list[OptionDescriptor]]:
    arguments: list[ArgumentDescriptor] = []
    options: list[OptionDescriptor] = []
    for parameter in command.params:
        if getattr(parameter, "hidden", False):
            continue
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
                    required=parameter.required
                    or bool(getattr(parameter, "required_for_projection", False)),
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
        if not path and _is_external_command(name):
            argv = [*_help_argv_prefix(), name]
            children.append(
                HelpSubcommand(
                    name=name,
                    summary=command_plugins.root_summary(name),
                    action=HelpNavigationAction(command=" ".join(argv), argv=argv),
                )
            )
            continue
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


def _command_for_path(
    path: tuple[str, ...], *, allow_external_commands: bool = True
) -> click.Command | None:
    if not path:
        return cli
    command = _load_command_with_policy(path[0], allow_external_commands=allow_external_commands)
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
            _context_for(argv, list(path), allow_external_commands=False),
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
    command = _command_for_path(tuple(path), allow_external_commands=False)
    if isinstance(command, click.Group):
        return [
            action(
                f"help-{child.name}",
                child.action.argv,
                f"Show {child.name} command help",
            )
            for child in _help_children(tuple(path), command)
        ]
    manifest_children = _manifest_help_children(tuple(path))
    if manifest_children:
        return [
            action(
                f"help-{child.name}",
                child.action.argv,
                f"Show {child.name} command help",
            )
            for child in manifest_children
        ]
    help_argv = _help_argv_prefix()
    if command is not None or command_plugins.operation(tuple(path)) is not None:
        help_argv = [*help_argv, *path]
    return [action("help", help_argv, "Show command usage")]


def entity_not_found(entity_type: str, requested_id: str) -> CliFailure:
    """Compatibility import for commands implemented before query extraction."""
    from infralink.cli.queries import entity_not_found as query_entity_not_found

    return query_entity_not_found(entity_type, requested_id)


_BUILTIN_COMMAND_NAMES = frozenset(
    {
        "help",
        "version",
        "analyze",
        "check",
        "doctor",
        "mcp",
        "diagram",
        "docs",
        "resolve",
        "validate",
        "fleet",
        "capabilities",
        "project",
        "explain",
        "app",
        "info",
        "hosts",
        "edges-list",
        "services",
        "host",
        "operation",
        "edge",
        "service",
        "secrets",
        "release",
        "registry",
    }
)


def _is_external_command(name: str) -> bool:
    """Whether a name resolves only through an installed command plugin."""
    return name not in _BUILTIN_COMMAND_NAMES and name in command_plugins.names()


def _load_command(
    name: str, *, allow_external_commands: bool | None = None
) -> click.Command | None:
    if allow_external_commands is None:
        allow_external_commands = _ALLOW_EXTERNAL_COMMANDS.get()
    if name == "help":
        return help_command
    if name == "version":
        return version_command
    if name == "doctor":
        from infralink.cli.doctor import doctor

        return doctor
    if name == "mcp":
        return mcp
    if name == "diagram":
        from infralink.cli.diagram import diagram

        return diagram
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
    if name == "hosts":
        return hosts
    if name == "edges-list":
        return edges_list
    if name == "services":
        return services
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
    if name == "registry":
        from infralink.cli.registry_authoring import registry

        return registry
    if not allow_external_commands:
        return None
    return command_plugins.load(name)


def _load_command_with_policy(name: str, *, allow_external_commands: bool) -> click.Command | None:
    token = _ALLOW_EXTERNAL_COMMANDS.set(allow_external_commands)
    try:
        return _load_command(name)
    finally:
        _ALLOW_EXTERNAL_COMMANDS.reset(token)


class JsonGroup(click.Group):
    def list_commands(self, ctx: click.Context) -> list[str]:
        # Discovery must be offline and side-effect free. In particular, an
        # entry point may import a controller runtime that pulls an image.
        # External commands remain available only through explicit execution.
        builtins = {
            name
            for name in COMMAND_METADATA
            if _load_command_with_policy(name, allow_external_commands=False) is not None
        }
        return sorted(builtins | command_plugins.names())

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = _load_command(cmd_name)
        if command is not None:
            # The root callback runs after Click has resolved the child. Keep
            # that exact mounted command so its default map reaches the active
            # operation tree rather than a second freshly-created tree.
            ctx.meta.setdefault("infralink_resolved_commands", {})[cmd_name] = command
            if cmd_name in {"analyze", "app", "check", "docs", "fleet", "host", "info", "resolve"}:
                # Extracted Agent Surface leaves parse after the public root
                # resolved the command. Seed their inherited error context
                # now so parse failures retain root output/source selections.
                ctx.meta.setdefault(
                    "agent_surface.raw_argv",
                    ("infralink", *redact_argv(list(current_invocation_argv()))),
                )
        return command

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        cmd_name, command, remaining = super().resolve_command(ctx, args)
        if cmd_name is not None and _is_external_command(cmd_name):
            _reject_mounted_overrides(remaining)
        return cmd_name, command, remaining

    def main(
        self,
        args: Sequence[str] | None = None,
        prog_name: str | None = None,
        complete_var: str | None = None,
        standalone_mode: bool = True,
        **extra: Any,
    ) -> Any:
        incoming = list(sys.argv[1:] if args is None else args)
        discovery_scope = command_plugins.discovery_scope()
        discovery_scope.__enter__()
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
                        _emit(
                            error_envelope(
                                _context_for(incoming, allow_external_commands=False), failure
                            )
                        )
                        exit_code = failure.exit_code
            except click.UsageError as usage_error:
                path, _, _ = _parse_invocation(redact_argv(incoming), allow_external_commands=False)
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
                    message=(
                        str(usage_error)
                        if isinstance(usage_error, MountedOverrideUsageError)
                        else "Invalid command usage"
                    ),
                    exit_code=ExitCode.USAGE_ERROR,
                    fix=(
                        "Provide an explicit safe relative --output directory"
                        if artifact_command is not None
                        else "Run infralink help"
                    ),
                    next_actions=_usage_actions(path, artifact_command),
                )
                if not continue_after_usage and not _ENVELOPE_EMITTED.get():
                    _emit(
                        error_envelope(
                            _context_for(incoming, allow_external_commands=False), usage_failure
                        )
                    )
                if not continue_after_usage:
                    exit_code = usage_failure.exit_code
            except CliFailure as cli_failure:
                if not _ENVELOPE_EMITTED.get():
                    _emit(
                        error_envelope(
                            _context_for(incoming, allow_external_commands=False), cli_failure
                        )
                    )
                exit_code = cli_failure.exit_code
            except SystemExit as system_exit:
                if _ENVELOPE_EMITTED.get() and isinstance(system_exit.code, int):
                    try:
                        exit_code = ExitCode(system_exit.code)
                    except ValueError:
                        failure = internal_failure()
                        _emit(
                            error_envelope(
                                _context_for(incoming, allow_external_commands=False), failure
                            )
                        )
                        exit_code = failure.exit_code
                else:
                    failure = internal_failure()
                    _emit(
                        error_envelope(
                            _context_for(incoming, allow_external_commands=False), failure
                        )
                    )
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
                    _emit(
                        error_envelope(
                            _context_for(incoming, allow_external_commands=False), failure
                        )
                    )
                    exit_code = failure.exit_code
        finally:
            pending_envelope = _PENDING_ENVELOPE.get()
            _PENDING_ENVELOPE.reset(pending_token)
            _DEFER_ENVELOPE.reset(deferred_token)
            _ENVELOPE_EMITTED.reset(emitted_token)
            _INVOCATION_ARGS.reset(invocation_token)
            discovery_scope.__exit__(None, None, None)
        if pending_envelope is not None:
            click.echo(pending_envelope)
        if standalone_mode:
            raise SystemExit(exit_code)
        return exit_code


def _set_mounted_defaults(ctx: click.Context, name: str, command: click.Command) -> None:
    """Pass the root's sole source/output selections into mounted operations."""
    runtime = ctx.find_root().obj
    if not isinstance(runtime, Context):
        return
    values: dict[str, Any] = {
        "registry": runtime.registry_path,
        "edges": runtime.edges_path,
        "_surface_format": runtime.output,
    }

    def defaults_for(current: click.Command) -> dict[str, Any]:
        if isinstance(current, click.Group):
            return {
                child_name: defaults_for(child)
                for child_name in current.list_commands(click.Context(current))
                if (child := current.get_command(click.Context(current), child_name)) is not None
            }
        return {
            parameter.name: values[parameter.name]
            for parameter in current.params
            if isinstance(parameter, click.Option) and parameter.name in values
        }

    default_map = dict(ctx.default_map or {})
    default_map[name] = defaults_for(command)
    ctx.default_map = default_map


def _reject_mounted_overrides(argv: list[str]) -> None:
    """Reject nested flags that would create a second output/source selector."""
    forbidden = {"--format", "--yaml-style", "--registry", "--edges"}
    for token in argv:
        option = token.partition("=")[0]
        if option in forbidden:
            raise MountedOverrideUsageError(
                f"{option} belongs to the root infralink command; provide it before the command path"
            )


class MountedOverrideUsageError(click.UsageError):
    """A fixed diagnostic for nested flags that would create another selector."""


LazyGroup = JsonGroup


@click.command(name="help")
@click.argument("path", nargs=-1)
def help_command(path: tuple[str, ...]) -> None:
    """Show machine-readable command help."""
    _emit_help(path)


@click.command(name="version")
def version_command() -> None:
    """Show CLI and schema versions."""
    from infralink.operator_surface import VersionRequest, version_operation

    _emit(
        ok_envelope(
            _context_for(path=["version"]),
            version_operation(VersionRequest()),
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
    help="Path to the registry checkout root",
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
def legacy_cli(
    ctx: Context, registry: Path | None, edges: Path | None, verbose: bool, output: str
) -> None:
    """
    Infralink - Infrastructure topology modeling.

    Manage infrastructure nodes and edges for health checks,
    diagram generation, and documentation.
    """
    click_ctx = click.get_current_context()
    source_independent = {None, "help", "version", "capabilities", "explain"}
    # ``diagram project`` consumes only explicit V2 observation sources. It
    # must ignore ambient root defaults but retain an explicitly supplied root
    # source so the child can reject it as unsupported input.
    incoming = _INVOCATION_ARGS.get() or []
    diagram_project = any(
        incoming[index : index + 2] == ["diagram", "project"] for index in range(len(incoming) - 1)
    )
    if diagram_project:
        source_independent.add("diagram")
    if diagram_project:
        selected_registry = (
            registry
            if click_ctx.get_parameter_source("registry") is click.core.ParameterSource.COMMANDLINE
            else None
        )
        selected_edges = (
            edges
            if click_ctx.get_parameter_source("edges") is click.core.ParameterSource.COMMANDLINE
            else None
        )
    else:
        selected_registry = registry
        if selected_registry is None and click_ctx.invoked_subcommand not in source_independent:
            selected_registry = _configured_registry()
        selected_edges = edges
        if selected_edges is None:
            registry_root = registry_checkout_root(selected_registry)
            if registry_root is not None:
                try:
                    selected_edges = resolve_registry_companion(registry_root, filename="edges.yml")
                except OperationError:
                    # Operations that require edges report the typed source error.
                    # Read-only registry-only commands remain usable without them.
                    pass
    ctx.registry_path = selected_registry
    ctx.hosts_path = None
    ctx.edges_path = selected_edges
    ctx.verbose = verbose
    ctx.output = output
    ctx.output_explicit = (
        click.get_current_context().get_parameter_source("output")
        is not click.core.ParameterSource.DEFAULT
    )

    mounted_commands = {"analyze", "app", "check", "docs", "fleet", "host", "info", "resolve"}
    if click_ctx.invoked_subcommand is not None and (
        _is_external_command(click_ctx.invoked_subcommand)
        or click_ctx.invoked_subcommand in mounted_commands
    ):
        mounted = click_ctx.meta.get("infralink_resolved_commands", {}).get(
            click_ctx.invoked_subcommand
        ) or _load_command(click_ctx.invoked_subcommand)
        if mounted is not None:
            _set_mounted_defaults(click_ctx, click_ctx.invoked_subcommand, mounted)

    click_ctx = click.get_current_context()
    if click_ctx.invoked_subcommand is not None:
        return

    live_commands = legacy_cli.list_commands(click_ctx)
    command_tree = RootResult(
        version=__version__,
        commands=[_command_descriptor(name) for name in live_commands],
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


@click.group(name="mcp")
def mcp() -> None:
    """Serve typed Infralink tools over Model Context Protocol stdio."""


@mcp.command(name="serve")
def mcp_serve() -> None:
    """Start the stdio MCP server."""
    from infralink.mcp_server import serve

    serve()


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
def operation() -> None:
    """Inspect durable host apply operations."""


@operation.command(name="status")
@click.argument("operation_id")
@pass_context
def operation_status(ctx: Context, operation_id: str) -> int:
    """Get the current state of one declared host-local reconcile operation."""
    from infralink.operator_surface import OperationStatusRequest, operation_status_operation

    try:
        result = operation_status_operation(
            OperationStatusRequest(
                registry=registry_checkout_root(ctx.registry_path) or ctx.registry_path,
                edges=ctx.edges_path,
                operation_id=operation_id,
            )
        )
    except Exception as error:
        _raise_cli_operation_error(error)
    actions = []
    if result.operation.state in {"queued", "applying"}:
        actions.append(
            action(
                "status",
                [*_root_action_prefix(ctx), "operation", "status", result.operation.id],
                "Check host apply progress",
            )
        )
    _emit(ok_envelope(_context_for(path=["operation", "status"]), result, actions))
    return 0 if result.operation.state != "failed" else 1


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


# The installed ``infralink`` executable is the Agent Surface projection itself.
# Do not wrap, copy, filter, or extend this tree: Click and MCP must derive from
# the same typed registry, including their generated discovery operations.
cli = operator_click_adapter().command()


def main(args: list[str] | None = None) -> int:
    incoming = list(sys.argv[1:] if args is None else args)
    # A bare executable is HATEOAS discovery, rather than Click's prose help.
    if not incoming:
        incoming = ["help"]
    result = cli.main(args=incoming, prog_name="infralink", standalone_mode=False)
    return 0 if result is None else int(result)


def run(args: list[str] | None = None) -> None:
    raise SystemExit(main(args))


if __name__ == "__main__":
    run()
