from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError

import pytest

from infralink.cli.actions import action
from infralink.cli.contracts import Action, Binding, CommandContext
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.output import (
    SENSITIVE_OPTIONS,
    command_context,
    error_envelope,
    ok_envelope,
    redact_argv,
)


def test_public_helper_signatures() -> None:
    assert list(inspect.signature(action).parameters) == [
        "rel",
        "argv",
        "description",
        "bindings",
    ]
    assert list(inspect.signature(command_context).parameters) == [
        "argv",
        "path",
        "args",
        "flags",
        "resolved",
    ]
    assert list(inspect.signature(ok_envelope).parameters) == [
        "context",
        "result",
        "next_actions",
        "status",
    ]
    assert list(inspect.signature(error_envelope).parameters) == [
        "context",
        "failure",
        "code",
        "fix",
        "next_actions",
    ]


def test_error_codes_are_stable_strings() -> None:
    assert {code.name: code.value for code in ErrorCode} == {
        "USAGE_ERROR": "usage_error",
        "INPUT_LOAD_FAILED": "input_load_failed",
        "ENTITY_NOT_FOUND": "entity_not_found",
        "INVALID_CURSOR": "invalid_cursor",
        "PROVIDER_UNAVAILABLE": "provider_unavailable",
        "PROVIDER_AUTHENTICATION_FAILED": "provider_authentication_failed",
        "PROVIDER_AUTHORIZATION_FAILED": "provider_authorization_failed",
        "PROVIDER_TIMEOUT": "provider_timeout",
        "INTERNAL_ERROR": "internal_error",
    }
    assert isinstance(ErrorCode.USAGE_ERROR, str)


def test_cli_failure_is_frozen_and_defaults_are_independent() -> None:
    first = CliFailure(ErrorCode.INTERNAL_ERROR, "boom", 1, "Retry")
    second = CliFailure(ErrorCode.INTERNAL_ERROR, "boom", 1, "Retry")

    first.details["request_id"] = "one"
    first.next_actions.append(action("retry", ["infralink"], "Retry"))

    assert second.details == {}
    assert second.next_actions == []
    with pytest.raises(FrozenInstanceError):
        first.message = "changed"


def test_action_is_typed_canonical_and_does_not_alias_inputs() -> None:
    argv = ["infralink", "host", "show", "host with spaces"]
    bindings = {
        "host_id": Binding(type="string", required=True, source="result.items[].id")
    }

    result = action("show", argv, "Show host", bindings=bindings)
    argv.append("--json")
    bindings.clear()

    assert isinstance(result, Action)
    assert result.argv == ["infralink", "host", "show", "host with spaces"]
    assert result.command == "infralink host show 'host with spaces'"
    assert result.safe is True
    assert result.templated is True
    assert result.bindings["host_id"].source == "result.items[].id"


def test_action_without_bindings_is_not_templated() -> None:
    first = action("check", ["infralink", "check"], "Run checks")
    second = action("check", ["infralink", "check"], "Run checks")

    assert first.templated is False
    assert first.bindings == {}
    assert first.bindings is not second.bindings


def test_sensitive_options_are_exact() -> None:
    assert SENSITIVE_OPTIONS == {
        "--access-token",
        "--password",
        "--password-env",
        "--token",
    }


@pytest.mark.parametrize("option", sorted(SENSITIVE_OPTIONS))
def test_redact_argv_redacts_separate_sensitive_values(option: str) -> None:
    secret = "canary-secret"

    assert redact_argv(["infralink", option, secret, "--verbose"]) == [
        "infralink",
        option,
        "[REDACTED]",
        "--verbose",
    ]


@pytest.mark.parametrize("option", sorted(SENSITIVE_OPTIONS))
def test_redact_argv_redacts_inline_sensitive_values(option: str) -> None:
    secret = "canary-secret"

    assert redact_argv(["infralink", f"{option}={secret}"]) == [
        "infralink",
        f"{option}=[REDACTED]",
    ]


def test_redact_argv_handles_trailing_sensitive_option() -> None:
    assert redact_argv(["infralink", "--token"]) == ["infralink", "--token"]


def test_redact_argv_preserves_non_sensitive_args_without_mutating_caller() -> None:
    argv = ["infralink", "--token-file", "secret.txt", "value with spaces"]

    assert redact_argv(argv) == argv
    assert argv == ["infralink", "--token-file", "secret.txt", "value with spaces"]


def test_command_context_is_typed_redacted_and_shell_escaped() -> None:
    argv = ["infralink", "secrets", "audit", "--token", "canary secret"]
    resolved = {"version": "0.2.0", "cwd": "/work"}

    context = command_context(
        argv,
        path=["secrets", "audit"],
        args={"provider": "bws"},
        flags=["--json"],
        resolved=resolved,
    )

    assert isinstance(context, CommandContext)
    assert context.raw == "infralink secrets audit --token '[REDACTED]'"
    assert "canary secret" not in context.raw
    assert context.parsed == {
        "path": ["secrets", "audit"],
        "args": {"provider": "bws"},
        "flags": ["--json"],
    }
    assert context.resolved == resolved


def test_ok_envelope_contains_structured_command_and_action() -> None:
    payload = ok_envelope(
        context=command_context(
            ["infralink", "validate"],
            path=["validate"],
            args={},
            flags=[],
            resolved={"version": "0.2.0", "cwd": "/work"},
        ),
        result={"valid": True},
        next_actions=[action("check", ["infralink", "check"], "Run checks")],
    )

    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["ok"] is True
    assert payload["command"]["parsed"]["path"] == ["validate"]
    assert payload["result"] == {"valid": True}
    assert "error" not in payload
    assert "fix" not in payload
    assert payload["next_actions"][0]["argv"] == ["infralink", "check"]


def test_failure_has_stable_exit_and_repair_action() -> None:
    failure = CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message="Edge not found",
        exit_code=3,
        fix="Run infralink edges-list",
        details={"entity_type": "edge", "requested_id": "missing"},
        next_actions=[action("list", ["infralink", "edges-list"], "List edges")],
    )

    payload = error_envelope(command_context(["infralink"], [], {}, [], {}), failure)

    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "entity_not_found",
        "message": "Edge not found",
        "details": {"entity_type": "edge", "requested_id": "missing"},
    }
    assert payload["fix"] == "Run infralink edges-list"
    assert "result" not in payload
    assert payload["next_actions"][0]["rel"] == "list"
    assert failure.exit_code == 3


def test_legacy_ok_envelope_remains_available_to_unmigrated_commands() -> None:
    payload = ok_envelope(
        "infralink validate",
        {"valid": True},
        [{"command": "infralink check", "description": "Run checks"}],
        status="warn",
    )

    assert payload == {
        "status": "warn",
        "ok": False,
        "command": "infralink validate",
        "result": {"valid": True},
        "next_actions": [{"command": "infralink check", "description": "Run checks"}],
    }


def test_legacy_error_envelope_remains_available_to_unmigrated_commands() -> None:
    payload = error_envelope(
        "infralink validate",
        "Validation failed",
        "VALIDATION_FAILED",
        "Fix inputs",
        [{"command": "infralink validate", "description": "Retry"}],
    )

    assert payload == {
        "status": "error",
        "ok": False,
        "command": "infralink validate",
        "error": {"message": "Validation failed", "code": "VALIDATION_FAILED"},
        "fix": "Fix inputs",
        "next_actions": [{"command": "infralink validate", "description": "Retry"}],
    }
