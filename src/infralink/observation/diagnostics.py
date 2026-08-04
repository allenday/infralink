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


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One repair-oriented finding with a stable machine-readable code."""

    code: str
    severity: Severity
    message: str
    location: SourceLocation
    identity: str | None = None
    next_actions: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiagnosticSet:
    """A deterministically ordered and bounded collection of diagnostics."""

    diagnostics: tuple[Diagnostic, ...]
    limit: int
    total_count: int
    truncated: bool

    @classmethod
    def from_diagnostics(cls, diagnostics: Iterable[Diagnostic], *, limit: int) -> DiagnosticSet:
        if limit < 0:
            raise ValueError("diagnostic limit must be non-negative")
        ordered = sorted(diagnostics, key=_diagnostic_sort_key)
        return cls(
            diagnostics=tuple(ordered[:limit]),
            limit=limit,
            total_count=len(ordered),
            truncated=len(ordered) > limit,
        )

    def __bool__(self) -> bool:
        return bool(self.diagnostics)

    def __iter__(self) -> Iterator[Diagnostic]:
        return iter(self.diagnostics)

    def __len__(self) -> int:
        return len(self.diagnostics)

    def __getitem__(self, index: int) -> Diagnostic:
        return self.diagnostics[index]


def _diagnostic_sort_key(diagnostic: Diagnostic) -> tuple[int, str, str, str, str]:
    severity_rank = {"error": 0, "warning": 1}
    return (
        severity_rank[diagnostic.severity],
        diagnostic.code,
        diagnostic.location.path,
        diagnostic.location.pointer,
        diagnostic.identity or "",
    )
