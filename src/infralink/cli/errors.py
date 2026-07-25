from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass, field
from enum import Enum, IntEnum
from types import MappingProxyType
from typing import Any, Final

from infralink.cli.contracts import Action


class ExitCode(IntEnum):
    POSITIVE_RESULT = 0
    NEGATIVE_RESULT = 1
    USAGE_ERROR = 2
    INPUT_ERROR = 3
    PROVIDER_ERROR = 4
    UNSUPPORTED_PLATFORM = 69
    INTERNAL_ERROR = 70
    ARTIFACT_IO_ERROR = 74


EXIT_CODE_MEANINGS: Final[Mapping[ExitCode, str]] = MappingProxyType(
    {
        ExitCode.POSITIVE_RESULT: "Positive domain result",
        ExitCode.NEGATIVE_RESULT: "Completed negative domain result",
        ExitCode.USAGE_ERROR: "Usage error",
        ExitCode.INPUT_ERROR: "Input, schema, or entity error",
        ExitCode.PROVIDER_ERROR: "Provider or authentication failure",
        ExitCode.UNSUPPORTED_PLATFORM: "Unsupported platform",
        ExitCode.INTERNAL_ERROR: "Unexpected internal failure",
        ExitCode.ARTIFACT_IO_ERROR: "Artifact I/O failure or retained recovery state",
    }
)

INTERNAL_ERROR_MESSAGE = "An unexpected internal error occurred"
INTERNAL_ERROR_FIX = "Retry the command or report the failure"


class ErrorCode(str, Enum):
    USAGE_ERROR = "usage_error"
    INPUT_LOAD_FAILED = "input_load_failed"
    ENTITY_NOT_FOUND = "entity_not_found"
    INVALID_CURSOR = "invalid_cursor"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    ARTIFACT_IO_FAILED = "artifact_io_failed"
    ARTIFACT_RECOVERY_REQUIRED = "artifact_recovery_required"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AUTHENTICATION_FAILED = "provider_authentication_failed"
    PROVIDER_AUTHORIZATION_FAILED = "provider_authorization_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass
class CliFailure(Exception):
    code: ErrorCode
    message: str
    exit_code: int
    fix: str
    details: dict[str, Any] = field(default_factory=dict)
    next_actions: list[Action] = field(default_factory=list)

    def __setattr__(self, name: str, value: Any) -> None:
        declared_fields = {
            "code",
            "message",
            "exit_code",
            "fix",
            "details",
            "next_actions",
        }
        runtime_fields = {
            "__cause__",
            "__context__",
            "__notes__",
            "__suppress_context__",
            "__traceback__",
        }
        if name in runtime_fields:
            object.__setattr__(self, name, value)
            return
        if name in declared_fields and not hasattr(self, name):
            object.__setattr__(self, name, value)
            return
        raise FrozenInstanceError(f"cannot assign to field {name!r}")

    def __delattr__(self, name: str) -> None:
        runtime_fields = {
            "__cause__",
            "__context__",
            "__notes__",
            "__suppress_context__",
            "__traceback__",
        }
        if name not in runtime_fields:
            raise FrozenInstanceError(f"cannot assign to field {name!r}")
        object.__delattr__(self, name)

    def __post_init__(self) -> None:
        try:
            normalized_exit_code = ExitCode(self.exit_code)
        except (TypeError, ValueError):
            object.__setattr__(self, "code", ErrorCode.INTERNAL_ERROR)
            object.__setattr__(self, "message", INTERNAL_ERROR_MESSAGE)
            object.__setattr__(self, "fix", INTERNAL_ERROR_FIX)
            object.__setattr__(self, "details", {})
            object.__setattr__(self, "next_actions", [])
            normalized_exit_code = ExitCode.INTERNAL_ERROR
        object.__setattr__(self, "exit_code", normalized_exit_code)
        object.__setattr__(self, "details", deepcopy(self.details))
        object.__setattr__(
            self,
            "next_actions",
            [repair.model_copy(deep=True) for repair in self.next_actions],
        )
        object.__setattr__(self, "args", (self.message,))

    def __reduce__(
        self,
    ) -> tuple[
        type[CliFailure],
        tuple[ErrorCode, str, int, str, dict[str, Any], list[Action]],
    ]:
        return (
            type(self),
            (
                self.code,
                self.message,
                self.exit_code,
                self.fix,
                self.details,
                self.next_actions,
            ),
        )


def internal_failure() -> CliFailure:
    return CliFailure(
        code=ErrorCode.INTERNAL_ERROR,
        message=INTERNAL_ERROR_MESSAGE,
        exit_code=ExitCode.INTERNAL_ERROR,
        fix=INTERNAL_ERROR_FIX,
    )
