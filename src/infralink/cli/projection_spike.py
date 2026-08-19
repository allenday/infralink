"""Compatibility spike helpers for comparing CLI and MCP projections."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import sys
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from infralink.cli.contracts import CommandContext
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.output import error_envelope

_REQUIRED_AGENT_SURFACE_VERSION = "0.1.0"
_PROOF_OPERATION_PATH = ["operation", "status"]
_COMPARED_KEYS = (
    "schema_version",
    "ok",
    "command",
    "result",
    "error",
    "fix",
    "next_actions",
    "meta",
)


@dataclass(frozen=True)
class AgentSurfaceAvailability:
    status: Literal["available", "unavailable"]
    reason: str
    python_version: str
    package_version: str | None = None


@dataclass(frozen=True)
class ProjectionComparison:
    operation: Literal["operation-status"]
    status: Literal["pass", "fail"]
    compared_keys: tuple[str, ...]
    mismatches: tuple[str, ...]


def probe_agent_surface(
    *,
    python_version: tuple[int, int] | None = None,
    import_name: str = "agent_surface",
    distribution_name: str = "agent-surface",
    required_version: str = _REQUIRED_AGENT_SURFACE_VERSION,
) -> AgentSurfaceAvailability:
    """Report whether the optional agent-surface proof dependency can be imported."""
    version_info = python_version or (sys.version_info.major, sys.version_info.minor)
    rendered_python = f"{version_info[0]}.{version_info[1]}"
    if version_info < (3, 12):
        return AgentSurfaceAvailability(
            status="unavailable",
            reason="agent-surface 0.1.0 is gated to Python 3.12 or newer",
            python_version=rendered_python,
        )
    if importlib.util.find_spec(import_name) is None:
        return AgentSurfaceAvailability(
            status="unavailable",
            reason="agent-surface 0.1.0 is not installed",
            python_version=rendered_python,
        )
    try:
        package_version = importlib.metadata.version(distribution_name)
    except importlib.metadata.PackageNotFoundError:
        package_version = None
    if package_version != required_version:
        return AgentSurfaceAvailability(
            status="unavailable",
            reason=f"agent-surface version is {package_version or 'unknown'}, expected {required_version}",
            python_version=rendered_python,
            package_version=package_version,
        )
    return AgentSurfaceAvailability(
        status="available",
        reason="agent-surface 0.1.0 import gate passed",
        python_version=rendered_python,
        package_version=package_version,
    )


def mcp_structured_content(cli_envelope: dict[str, Any]) -> dict[str, Any]:
    """Project a CLI envelope exactly as MCP structured content."""
    return deepcopy(cli_envelope)


def compare_operation_status_projection(
    cli_envelope: dict[str, Any], mcp_envelope: dict[str, Any]
) -> ProjectionComparison:
    """Compare the operation-status fields that must not drift between CLI and MCP."""
    parsed = cli_envelope.get("command", {}).get("parsed", {})
    if parsed.get("path") != _PROOF_OPERATION_PATH:
        mismatches = ("unsupported operation projection: expected operation status",)
        return ProjectionComparison(
            operation="operation-status",
            status="fail",
            compared_keys=_COMPARED_KEYS,
            mismatches=mismatches,
        )

    mismatches = tuple(
        key for key in _COMPARED_KEYS if cli_envelope.get(key) != mcp_envelope.get(key)
    )
    return ProjectionComparison(
        operation="operation-status",
        status="pass" if not mismatches else "fail",
        compared_keys=_COMPARED_KEYS,
        mismatches=mismatches,
    )


def unsupported_projection_envelope(context: CommandContext, operation: str) -> dict[str, Any]:
    """Return a typed fail-closed envelope for operations outside this spike."""
    return error_envelope(
        context,
        CliFailure(
            code=ErrorCode.USAGE_ERROR,
            message="Unsupported projection target",
            exit_code=ExitCode.USAGE_ERROR,
            fix="Use the existing CLI command until this operation is explicitly projected",
            details={"operation": operation, "supported": ["operation status"]},
        ),
    )
