"""Stable diagnostics for offline observation contract processing."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Literal

Severity = Literal["error", "warning"]


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """A source file and an unambiguous JSON-pointer-like location within it."""

    path: str
    pointer: str = "/"

    def __post_init__(self) -> None:
        if not isinstance(self.path, str) or not self.path:
            raise ValueError("source location path must not be empty")
        if not isinstance(self.pointer, str) or not self.pointer.startswith("/"):
            raise ValueError("source location pointer must start with '/'")


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One repair-oriented finding with a stable machine-readable code."""

    code: str
    severity: Severity
    message: str
    location: SourceLocation
    identity: str | None = None
    next_actions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity not in ("error", "warning"):
            raise ValueError("diagnostic severity must be 'error' or 'warning'")
        if not isinstance(self.code, str) or not self.code:
            raise ValueError("diagnostic code must not be empty")
        if not isinstance(self.message, str) or not self.message:
            raise ValueError("diagnostic message must not be empty")
        if not isinstance(self.location, SourceLocation):
            raise ValueError("diagnostic location must be a SourceLocation")
        if self.identity is not None and not isinstance(self.identity, str):
            raise ValueError("diagnostic identity must be a string or None")
        if not isinstance(self.next_actions, tuple) or not all(
            isinstance(action, str) and action for action in self.next_actions
        ):
            raise ValueError("diagnostic next_actions must be a tuple of non-empty strings")


@dataclass(frozen=True, slots=True)
class DiagnosticSet:
    """A deterministically ordered and bounded collection of diagnostics."""

    diagnostics: tuple[Diagnostic, ...]
    limit: int
    total_count: int
    truncated: bool
    error_count: int
    warning_count: int

    def __post_init__(self) -> None:
        if not isinstance(self.diagnostics, tuple):
            raise ValueError("diagnostics must be a tuple")
        counts = (self.limit, self.total_count, self.error_count, self.warning_count)
        if any(type(value) is not int or value < 0 for value in counts):
            raise ValueError("diagnostic limit and counts must be non-negative")
        if type(self.truncated) is not bool:
            raise ValueError("truncated must be a bool")
        if not all(isinstance(item, Diagnostic) for item in self.diagnostics):
            raise ValueError("diagnostics must contain only Diagnostic values")
        if self.error_count + self.warning_count != self.total_count:
            raise ValueError("diagnostic severity counts must equal total_count")
        expected_retained = min(self.limit, self.total_count)
        if len(self.diagnostics) != expected_retained:
            raise ValueError("retained diagnostics must equal min(limit, total_count)")
        if self.truncated != (self.total_count > self.limit):
            raise ValueError("truncated must reflect whether total_count exceeds limit")

    @classmethod
    def from_diagnostics(cls, diagnostics: Iterable[Diagnostic], *, limit: int) -> DiagnosticSet:
        if type(limit) is not int or limit < 0:
            raise ValueError("diagnostic limit must be non-negative")
        ordered = sorted(diagnostics, key=_diagnostic_sort_key)
        error_count = sum(item.severity == "error" for item in ordered)
        return cls(
            diagnostics=tuple(ordered[:limit]),
            limit=limit,
            total_count=len(ordered),
            truncated=len(ordered) > limit,
            error_count=error_count,
            warning_count=len(ordered) - error_count,
        )

    def __bool__(self) -> bool:
        return bool(self.diagnostics)

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self.diagnostics)

    def __len__(self) -> int:
        return len(self.diagnostics)

    def __getitem__(self, index: int) -> Diagnostic:
        return self.diagnostics[index]


def _diagnostic_sort_key(
    diagnostic: Diagnostic,
) -> tuple[int, str, str, str, str, str, tuple[str, ...]]:
    severity_rank = {"error": 0, "warning": 1}
    return (
        severity_rank[diagnostic.severity],
        diagnostic.code,
        diagnostic.location.path,
        diagnostic.location.pointer,
        diagnostic.identity or "",
        diagnostic.message,
        tuple(sorted(diagnostic.next_actions)),
    )
