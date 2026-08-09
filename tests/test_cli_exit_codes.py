from __future__ import annotations

import json

import click
import pytest
import yaml
from click.testing import CliRunner

from infralink.cli.contracts import CommandContext
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.main import JsonGroup, _emit
from infralink.cli.output import error_envelope


def test_cli_failure_normalizes_unregistered_exit_code_to_internal_error() -> None:
    failure = CliFailure(
        code=ErrorCode.USAGE_ERROR,
        message="unregistered-exit-canary",
        exit_code=75,
        fix="unregistered-fix-canary",
        details={"canary": "unregistered-detail-canary"},
    )

    assert failure.code is ErrorCode.INTERNAL_ERROR
    assert failure.message == "An unexpected internal error occurred"
    assert failure.exit_code is ExitCode.INTERNAL_ERROR
    assert failure.fix == "Retry the command or report the failure"
    assert failure.details == {}
    assert failure.next_actions == []
    serialized = json.dumps(
        error_envelope(
            CommandContext(raw="infralink", parsed={}, resolved={}),
            failure,
        )
    )
    assert "unregistered-exit-canary" not in serialized
    assert '"code": "internal_error"' in serialized


@pytest.mark.parametrize("exit_code", list(ExitCode))
def test_cli_failure_preserves_registered_exit_codes(exit_code: ExitCode) -> None:
    failure = CliFailure(
        code=ErrorCode.INTERNAL_ERROR,
        message="registered",
        exit_code=int(exit_code),
        fix="registered-fix",
        details={"registered": True},
    )

    assert failure.code is ErrorCode.INTERNAL_ERROR
    assert failure.message == "registered"
    assert failure.exit_code is exit_code
    assert failure.fix == "registered-fix"
    assert failure.details == {"registered": True}


def test_json_group_fails_closed_on_unregistered_callback_exit_code() -> None:
    @click.group(cls=JsonGroup, invoke_without_command=True)
    def command() -> int:
        return 75

    result = CliRunner().invoke(command, [])

    assert result.exit_code == ExitCode.INTERNAL_ERROR
    assert result.stderr == ""
    payload = yaml.safe_load(result.output)
    assert payload["schema_version"] == "infralink.cli/v1"
    assert payload["ok"] is False
    assert payload["error"] == {
        "code": "internal_error",
        "message": "An unexpected internal error occurred",
        "details": {},
    }


@pytest.mark.parametrize("terminal", ["return", "system_exit"])
def test_json_group_discards_buffered_envelope_on_unregistered_exit(
    terminal: str,
) -> None:
    canary = f"unsafe-buffered-envelope-{terminal}"

    @click.group(cls=JsonGroup, invoke_without_command=True)
    def command() -> int | None:
        _emit({"unsafe": canary})
        if terminal == "system_exit":
            raise SystemExit(75)
        return 75

    result = CliRunner().invoke(command, [])

    assert result.exit_code == ExitCode.INTERNAL_ERROR
    assert result.stderr == ""
    assert canary not in result.output
    payload = yaml.safe_load(result.output)
    assert payload["error"]["code"] == "internal_error"
    assert payload["error"]["message"] == "An unexpected internal error occurred"


def test_json_group_discards_buffered_envelope_on_unexpected_exception() -> None:
    canary = "unsafe-buffered-runtime-error"

    @click.group(cls=JsonGroup, invoke_without_command=True)
    def command() -> None:
        _emit({"unsafe": canary})
        raise RuntimeError(canary)

    result = CliRunner().invoke(command, [])

    assert result.exit_code == ExitCode.INTERNAL_ERROR
    assert result.stderr == ""
    assert canary not in result.output
    payload = yaml.safe_load(result.output)
    assert payload["error"] == {
        "code": "internal_error",
        "message": "An unexpected internal error occurred",
        "details": {},
    }


@pytest.mark.parametrize("exit_code", list(ExitCode))
def test_json_group_preserves_registered_callback_exit_codes(exit_code: ExitCode) -> None:
    @click.group(cls=JsonGroup, invoke_without_command=True)
    def command() -> int:
        return int(exit_code)

    assert command.main(args=[], standalone_mode=False) is exit_code


@pytest.mark.parametrize("terminal", ["return", "system_exit"])
@pytest.mark.parametrize("exit_code", list(ExitCode))
def test_json_group_flushes_one_envelope_for_registered_exit(
    terminal: str,
    exit_code: ExitCode,
) -> None:
    @click.group(cls=JsonGroup, invoke_without_command=True)
    def command() -> int | None:
        _emit({"registered": int(exit_code)})
        if terminal == "system_exit":
            raise SystemExit(exit_code)
        return int(exit_code)

    result = CliRunner().invoke(command, [])

    assert result.exit_code == exit_code
    assert result.stderr == ""
    assert yaml.safe_load(result.output) == {"registered": int(exit_code)}
