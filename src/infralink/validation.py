"""Validation result helpers for infralink public API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class ValidationError(ValueError):
    """Hard validation failure."""


class ValidationWarning(UserWarning):
    """Soft validation warning."""


@dataclass(frozen=True)
class ValidationResult:
    """Result container for validation output."""

    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors

    @classmethod
    def from_iterables(
        cls,
        errors: Iterable[str] | None = None,
        warnings: Iterable[str] | None = None,
    ) -> "ValidationResult":
        return cls(tuple(errors or ()), tuple(warnings or ()))
