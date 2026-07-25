from __future__ import annotations

from copy import deepcopy
from dataclasses import FrozenInstanceError, dataclass, field
from enum import Enum
from typing import Any

from infralink.cli.contracts import Action


class ErrorCode(str, Enum):
    USAGE_ERROR = "usage_error"
    INPUT_LOAD_FAILED = "input_load_failed"
    ENTITY_NOT_FOUND = "entity_not_found"
    INVALID_CURSOR = "invalid_cursor"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
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
