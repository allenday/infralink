from __future__ import annotations

import importlib.metadata
import importlib.util

from infralink.cli.actions import action
from infralink.cli.contracts import CommandContext, DoctorTarget
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.operation_contracts import OperationStatusResult, OperationSummary
from infralink.cli.output import command_context, error_envelope, ok_envelope
from infralink.cli.projection_spike import (
    compare_operation_status_projection,
    mcp_structured_content,
    probe_agent_surface,
    unsupported_projection_envelope,
)
from tests.cli_helpers import assert_schema

HOST_ID = "32a3324f-c3d0-4a4f-9587-52c099bcb3fb"
INVOCATION = "0123456789abcdef0123456789abcdef"
OPERATION_ID = f"ssh/{HOST_ID}/{INVOCATION}"


def _operation_status_context() -> CommandContext:
    return command_context(
        [
            "infralink",
            "--registry",
            "/tmp/registry",
            "operation",
            "status",
            OPERATION_ID,
        ],
        ["operation", "status"],
        {"operation_id": OPERATION_ID},
        ["--registry"],
        {"registry": "/tmp/registry"},
    )


def _operation_status_payload() -> dict[str, object]:
    context = _operation_status_context()
    return ok_envelope(
        context,
        OperationStatusResult(
            operation=OperationSummary(id=OPERATION_ID, state="applying"),
            target=DoctorTarget(type="host", id=HOST_ID, canonical_name="relayos-staging"),
        ),
        [
            action(
                "status",
                ["infralink", "--registry", "/tmp/registry", "operation", "status", OPERATION_ID],
                "Check host apply progress",
            )
        ],
    )


def test_operation_status_cli_and_mcp_projection_envelopes_match() -> None:
    cli_payload = _operation_status_payload()
    mcp_payload = mcp_structured_content(cli_payload)

    comparison = compare_operation_status_projection(cli_payload, mcp_payload)

    assert comparison.status == "pass"
    assert comparison.mismatches == ()
    assert cli_payload["schema_version"] == "infralink.cli/v1"
    assert cli_payload["ok"] is True
    assert cli_payload["command"]["parsed"]["path"] == ["operation", "status"]
    assert cli_payload["next_actions"] == [
        {
            "rel": "status",
            "command": f"infralink --registry /tmp/registry operation status {OPERATION_ID}",
            "description": "Check host apply progress",
            "safe": True,
        }
    ]
    assert_schema(cli_payload, "operation-status")


def test_projection_comparison_detects_mcp_envelope_drift() -> None:
    cli_payload = _operation_status_payload()
    mcp_payload = mcp_structured_content(cli_payload)
    mcp_payload["next_actions"] = []

    comparison = compare_operation_status_projection(cli_payload, mcp_payload)

    assert comparison.status == "fail"
    assert comparison.mismatches == ("next_actions",)


def test_legacy_operation_id_error_envelope_can_project_without_drift() -> None:
    context = command_context(
        ["infralink", "operation", "status", "op_01J00000000000000000000000"],
        ["operation", "status"],
        {"operation_id": "op_01J00000000000000000000000"},
        [],
        {},
    )
    cli_payload = error_envelope(
        context,
        CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Legacy control-plane operation status is unavailable",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Start a new declared host-local apply operation",
            details={"operation_id": "op_01J00000000000000000000000"},
        ),
    )

    comparison = compare_operation_status_projection(
        cli_payload, mcp_structured_content(cli_payload)
    )

    assert comparison.status == "pass"
    assert cli_payload["ok"] is False
    assert cli_payload["error"]["code"] == "provider_unavailable"


def test_unsupported_projection_returns_typed_fail_closed_envelope() -> None:
    payload = unsupported_projection_envelope(_operation_status_context(), "host apply")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "usage_error"
    assert payload["error"]["details"] == {
        "operation": "host apply",
        "supported": ["operation status"],
    }
    assert payload["next_actions"] == []


def test_agent_surface_probe_fails_closed_before_python_312() -> None:
    availability = probe_agent_surface(python_version=(3, 11))

    assert availability.status == "unavailable"
    assert "Python 3.12" in availability.reason


def test_agent_surface_probe_fails_closed_when_dependency_missing(
    monkeypatch,
) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)

    availability = probe_agent_surface(python_version=(3, 12))

    assert availability.status == "unavailable"
    assert availability.reason == "agent-surface 0.1.0 is not installed"


def test_agent_surface_probe_fails_closed_on_version_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.2.0")

    availability = probe_agent_surface(python_version=(3, 12))

    assert availability.status == "unavailable"
    assert availability.package_version == "0.2.0"
    assert "expected 0.1.0" in availability.reason
