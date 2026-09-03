from __future__ import annotations

import copy
import json
import pickle
from collections.abc import Callable
from dataclasses import FrozenInstanceError, dataclass
from importlib.metadata import version
from pathlib import Path

import pytest
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from pydantic import BaseModel

from infralink.cli.actions import action
from infralink.cli.contracts import (
    Action,
    Binding,
    CheckCommandResult,
    CheckResult,
    CommandContext,
    Page,
    PageInfo,
)
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import cli
from infralink.cli.output import (
    SENSITIVE_OPTIONS,
    command_context,
    error_envelope,
    ok_envelope,
    redact_argv,
)


class NestedCredential(BaseModel):
    token: str
    metadata: dict[str, str]


@dataclass
class DataclassCredential:
    password_env: str


class OpaqueCredential:
    def __init__(self, token: str) -> None:
        self.token = token


def test_error_codes_are_stable_strings() -> None:
    assert {code.name: code.value for code in ErrorCode} == {
        "USAGE_ERROR": "usage_error",
        "AUTHORING_CHECKOUT_REQUIRED": "authoring_checkout_required",
        "CONFIGURATION_REQUIRED": "configuration_required",
        "INPUT_LOAD_FAILED": "input_load_failed",
        "ENTITY_NOT_FOUND": "entity_not_found",
        "INVALID_CURSOR": "invalid_cursor",
        "UNSUPPORTED_PLATFORM": "unsupported_platform",
        "ARTIFACT_IO_FAILED": "artifact_io_failed",
        "ARTIFACT_RECOVERY_REQUIRED": "artifact_recovery_required",
        "PROVIDER_UNAVAILABLE": "provider_unavailable",
        "PROVIDER_AUTHENTICATION_FAILED": "provider_authentication_failed",
        "PROVIDER_AUTHORIZATION_FAILED": "provider_authorization_failed",
        "PROVIDER_TIMEOUT": "provider_timeout",
        "RELEASE_VALIDATION_INVALID": "release_validation_invalid",
        "RELEASE_ADMISSION_REJECTED": "release_admission_rejected",
        "RELEASE_CANDIDATE_INVALID": "release_candidate_invalid",
        "RELEASE_PUBLISHER_REQUEST_INVALID": "release_publisher_request_invalid",
        "RELEASE_PUBLISHER_UNAVAILABLE": "release_publisher_unavailable",
        "RELEASE_ATTESTATION_INVALID": "release_attestation_invalid",
        "DIAGRAM_SCOPE_SELECTOR_INVALID": "diagram_scope_selector_invalid",
        "DIAGRAM_PROJECT_FORBIDDEN_INPUT": "diagram_project_forbidden_input",
        "DIAGRAM_SOURCE_INVALID": "diagram_source_invalid",
        "DIAGRAM_TOPOLOGY_BOUNDS_EXCEEDED": "diagram_topology_bounds_exceeded",
        "DIAGRAM_RENDER_BOUNDS_EXCEEDED": "diagram_render_bounds_exceeded",
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


def test_cli_failure_only_allows_exception_runtime_mutation() -> None:
    failure = CliFailure(ErrorCode.INTERNAL_ERROR, "boom", 70, "Retry")

    failure.__traceback__ = None
    failure.__context__ = None
    failure.__cause__ = None
    failure.__suppress_context__ = False

    for operation in (
        lambda: setattr(failure, "args", ("changed",)),
        lambda: setattr(failure, "arbitrary", "value"),
        lambda: delattr(failure, "message"),
        lambda: delattr(failure, "args"),
    ):
        with pytest.raises(FrozenInstanceError):
            operation()


def test_cli_failure_copies_boundaries_and_logs_only_its_message() -> None:
    details = {"attempt": {"hosts": ["one"]}}
    repair = action("retry", ["infralink", "check"], "Retry")
    repairs = [repair]

    failure = CliFailure(
        ErrorCode.INTERNAL_ERROR,
        "Health check failed",
        1,
        "Retry",
        details,
        repairs,
    )
    details["attempt"]["hosts"].append("two")
    repair.argv.append("--mutated")
    repairs.clear()

    assert failure.details == {"attempt": {"hosts": ["one"]}}
    assert failure.next_actions[0].argv == ["infralink", "check"]
    assert str(failure) == "Health check failed"
    assert failure.args == ("Health check failed",)


@pytest.mark.parametrize(
    "roundtrip",
    [
        copy.copy,
        copy.deepcopy,
        lambda failure: pickle.loads(pickle.dumps(failure)),
    ],
    ids=["copy", "deepcopy", "pickle"],
)
def test_cli_failure_reconstructs_with_isolated_nested_data(
    roundtrip: Callable[[CliFailure], CliFailure],
) -> None:
    original = CliFailure(
        ErrorCode.INTERNAL_ERROR,
        "Health check failed",
        1,
        "Retry",
        {"attempt": {"hosts": ["one"]}},
        [action("retry", ["infralink", "check"], "Retry")],
    )

    reconstructed = roundtrip(original)
    reconstructed.details["attempt"]["hosts"].append("two")
    reconstructed.next_actions[0].argv.append("--mutated")

    assert reconstructed.code is ErrorCode.INTERNAL_ERROR
    assert reconstructed.exit_code == 1
    assert reconstructed.fix == "Retry"
    assert str(reconstructed) == "Health check failed"
    assert reconstructed.args == ("Health check failed",)
    assert original.details == {"attempt": {"hosts": ["one"]}}
    assert original.next_actions[0].argv == ["infralink", "check"]


def test_action_is_typed_canonical_and_does_not_alias_inputs() -> None:
    argv = ["infralink", "host", "show", "host with spaces"]
    bindings = {"host_id": Binding(type="string", required=True, source="result.items[].id")}

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


def test_action_can_explicitly_mark_mutating_commands_unsafe() -> None:
    result = action(
        "continue",
        ["infralink", "diagram", "--output", "generated"],
        "Regenerate the next artifact page",
        safe=False,
    )

    assert result.safe is False


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


def test_redact_argv_redacts_live_short_password_alias() -> None:
    assert redact_argv(["infralink", "-p", "canary-secret"]) == [
        "infralink",
        "-p",
        "[REDACTED]",
    ]
    assert redact_argv(["infralink", "-p=canary-secret"]) == [
        "infralink",
        "-p=[REDACTED]",
    ]


def test_public_click_parser_rejects_unknown_sensitive_option_without_echoing_it() -> None:
    result = CliRunner().invoke(
        cli,
        ["resolve", "edge-1", "--password=canary-secret"],
    )

    if tuple(map(int, version("agent-surface").split(".")[:2])) >= (0, 2):
        assert result.exit_code == 2
    assert "canary-secret" not in result.output


def test_redact_argv_redacts_attached_short_password_without_matching_long_prefixes() -> None:
    assert redact_argv(["infralink", "resolve", "edge-1", "-pcanary-secret"]) == [
        "infralink",
        "resolve",
        "edge-1",
        "-p[REDACTED]",
    ]
    assert redact_argv(["infralink", "--profile=canary-secret"]) == [
        "infralink",
        "--profile=canary-secret",
    ]


def test_redact_argv_does_not_consume_adjacent_sensitive_options() -> None:
    redacted = redact_argv(["infralink", "--token", "--password", "canary-secret"])

    assert redacted == ["infralink", "--token", "--password", "[REDACTED]"]
    assert "canary-secret" not in redacted


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


def test_command_context_recursively_sanitizes_and_copies_nested_values() -> None:
    args = {
        "credentials": {
            "Access-Token": "canary-secret",
            "items": [{"PASSWORD_ENV": "canary-secret"}],
        },
        "ordinary": {"values": ["before"]},
    }
    resolved = {
        "provider": {
            "token": "canary-secret",
            "nested": {"PassWord": "canary-secret"},
        }
    }

    context = command_context(["infralink"], [], args, [], resolved)
    args["ordinary"]["values"].append("after")
    resolved["provider"]["nested"]["status"] = "changed"
    payload = ok_envelope(context, {"valid": True}, [])
    serialized = json.dumps(payload)

    assert "canary-secret" not in serialized
    assert payload["command"]["parsed"]["args"]["credentials"] == {
        "Access-Token": "[REDACTED]",
        "items": [{"PASSWORD_ENV": "[REDACTED]"}],
    }
    assert payload["command"]["resolved"]["provider"] == {
        "token": "[REDACTED]",
        "nested": {"PassWord": "[REDACTED]"},
    }
    assert payload["command"]["parsed"]["args"]["ordinary"] == {"values": ["before"]}


def test_command_context_sanitizes_supported_models_in_success_and_error_envelopes() -> None:
    model = NestedCredential(
        token="canary-secret",
        metadata={"Access-Token": "canary-secret", "region": "west"},
    )
    structured = {
        "model": model,
        "dataclass": DataclassCredential(password_env="canary-secret"),
    }

    context = command_context(["infralink"], [], structured, [], {"models": [model]})
    model.metadata["region"] = "changed"
    success = ok_envelope(context, {"valid": True}, [])
    failure = error_envelope(
        context,
        CliFailure(ErrorCode.INTERNAL_ERROR, "Failed", 1, "Retry"),
    )

    assert "canary-secret" not in json.dumps(success)
    assert "canary-secret" not in json.dumps(failure)
    assert success["command"]["parsed"]["args"] == {
        "model": {
            "token": "[REDACTED]",
            "metadata": {"Access-Token": "[REDACTED]", "region": "west"},
        },
        "dataclass": {"password_env": "[REDACTED]"},
    }
    assert failure["command"] == success["command"]


def test_command_context_rejects_unsupported_structured_values() -> None:
    with pytest.raises(TypeError, match="unsupported command context value"):
        command_context(
            ["infralink"],
            [],
            {"credential": OpaqueCredential("canary-secret")},
            [],
            {},
        )


def test_command_context_rejects_non_string_mapping_keys() -> None:
    with pytest.raises(TypeError, match="non-string mapping key"):
        command_context(["infralink"], [], {"nested": {1: "value"}}, [], {})


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (
            lambda: ok_envelope(
                command="infralink",
                context="infralink",
                result={},
                next_actions=[],
            ),
            "either context or command",
        ),
        (
            lambda: ok_envelope(result={}),
            "context and next_actions are required",
        ),
        (
            lambda: error_envelope(
                command="infralink",
                context="infralink",
                message="failed",
                code="internal_error",
                fix="Retry",
                next_actions=[],
            ),
            "either context or command",
        ),
        (
            lambda: error_envelope(
                command="infralink",
                failure="failed",
                message="failed",
                code="internal_error",
                fix="Retry",
                next_actions=[],
            ),
            "either failure or message",
        ),
        (
            lambda: error_envelope(
                failure="failed",
                code="internal_error",
                fix="Retry",
                next_actions=[],
            ),
            "context is required",
        ),
        (
            lambda: error_envelope(context="infralink", failure="failed"),
            "legacy error envelope requires",
        ),
        (
            lambda: error_envelope(
                command_context(["infralink"], [], {}, [], {}),
                "failed",
            ),
            "v1 error envelope requires",
        ),
    ],
)
def test_envelope_boundaries_reject_ambiguous_or_incomplete_calls(
    call: Callable[[], dict[str, object]],
    message: str,
) -> None:
    with pytest.raises(TypeError, match=message):
        call()


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
    assert payload["next_actions"][0] == {
        "rel": "check",
        "command": "infralink check",
        "description": "Run checks",
        "safe": True,
    }


def test_rendered_actions_inherit_resolved_sources_and_keep_templated_contracts() -> None:
    context = command_context(
        ["infralink", "doctor", "host", "watchtower", "--validate"],
        path=["doctor", "host"],
        args={},
        flags=[],
        resolved={
            "registry": "/registry/hosts",
            "edges": "/registry/edges.yml",
            "observation_plan": "/registry/plan.json",
            "adapter_bindings": "/registry/bindings.yml",
        },
    )
    payload = ok_envelope(
        context=context,
        result={"status": "unknown"},
        next_actions=[
            action(
                "verbose",
                [
                    "infralink",
                    "--verbose",
                    "--registry",
                    "/registry/hosts",
                    "--edges",
                    "/registry/edges.yml",
                    "doctor",
                    "--observation-plan",
                    "/registry/plan.json",
                    "--adapter-bindings",
                    "/registry/bindings.yml",
                    "host",
                    "watchtower",
                    "--validate",
                ],
                "Show complete evidence",
            ),
            action(
                "show",
                ["infralink", "host", "show", "{id}"],
                "Show host",
                bindings={"id": Binding(type="string", required=True, source="result.items[]")},
            ),
        ],
    )

    assert payload["next_actions"] == [
        {
            "rel": "verbose",
            "command": "infralink --verbose doctor host watchtower --validate",
            "description": "Show complete evidence",
            "safe": True,
        },
        {
            "rel": "show",
            "command": "infralink host show '{id}'",
            "description": "Show host",
            "safe": True,
            "templated": True,
            "bindings": {"id": {"type": "string", "required": True, "source": "result.items[]"}},
        },
    ]


@pytest.mark.parametrize("token_option", ["--token", "--token=super-secret"])
def test_rendered_action_redacts_sensitive_option_values(token_option: str) -> None:
    argv = ["infralink", "release", token_option]
    if "=" not in token_option:
        argv.append("super-secret")

    payload = ok_envelope(
        context=command_context(
            ["infralink", "release"],
            path=["release"],
            args={},
            flags=[],
            resolved={},
        ),
        result={"ok": True},
        next_actions=[action("retry", argv, "Retry")],
    )

    rendered = payload["next_actions"][0]["command"]
    assert "super-secret" not in rendered
    assert "[REDACTED]" in rendered


def test_ok_envelope_preserves_required_nullable_result_fields() -> None:
    result = CheckCommandResult(
        healthy=False,
        checks=Page[CheckResult](
            items=[
                CheckResult(
                    edge_id="edge-1",
                    healthy=False,
                    status="unavailable",
                    latency_ms=None,
                    error_code=None,
                )
            ],
            page=PageInfo(limit=100, returned=1, total=1, next_cursor=None),
        ),
        summary={"total": 1, "healthy": 0, "unhealthy": 1},
    )

    payload = ok_envelope(
        command_context(["infralink", "check"], ["check"], {}, [], {}), result, []
    )
    schema = json.loads(
        (Path(__file__).parents[1] / "src/infralink/schemas/cli/v1/check.json").read_text(
            encoding="utf-8"
        )
    )

    Draft202012Validator(schema).validate(payload)
    assert payload["result"]["checks"]["items"][0]["latency_ms"] is None
    assert payload["result"]["checks"]["items"][0]["error_code"] is None
    assert "error" not in payload
    assert "fix" not in payload


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


def test_legacy_envelopes_accept_original_keyword_names() -> None:
    ok_payload = ok_envelope(
        command="infralink validate",
        result={"valid": True},
        next_actions=[],
    )
    error_payload = error_envelope(
        command="infralink validate",
        message="Validation failed",
        code="VALIDATION_FAILED",
        fix="Fix inputs",
        next_actions=[],
    )

    assert ok_payload["command"] == "infralink validate"
    assert error_payload["error"]["message"] == "Validation failed"
