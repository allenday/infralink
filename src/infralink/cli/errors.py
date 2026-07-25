from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from infralink.cli.contracts import Action


class ErrorCode(str, Enum):
    USAGE_ERROR = "usage_error"
    INPUT_LOAD_FAILED = "input_load_failed"
    ENTITY_NOT_FOUND = "entity_not_found"
    INVALID_CURSOR = "invalid_cursor"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_AUTHENTICATION_FAILED = "provider_authentication_failed"
    PROVIDER_AUTHORIZATION_FAILED = "provider_authorization_failed"
    PROVIDER_TIMEOUT = "provider_timeout"
    INTERNAL_ERROR = "internal_error"


@dataclass(frozen=True)
class CliFailure(Exception):
    code: ErrorCode
    message: str
    exit_code: int
    fix: str
    details: dict[str, Any] = field(default_factory=dict)
    next_actions: list[Action] = field(default_factory=list)

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
