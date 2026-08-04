"""Stable explanations for observation diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from infralink.observation.codes import ALL_DIAGNOSTIC_CODES

DIAGNOSTIC_CODES = tuple(sorted(ALL_DIAGNOSTIC_CODES))


@dataclass(frozen=True, slots=True)
class DiagnosticExplanation:
    code: str
    meaning: str
    affected_identity_types: tuple[str, ...]
    likely_causes: tuple[str, ...]
    next_actions: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


class DiagnosticCodeNotFoundError(LookupError):
    """A diagnostic code is unknown to this version of the library."""

    def __init__(self, code: str) -> None:
        self.code = code
        self.available_codes = DIAGNOSTIC_CODES
        self.suggestion = "Inspect available_codes to discover supported diagnostics."
        super().__init__(f"unknown observation diagnostic code: {code}")


def _identity_types(code: str) -> tuple[str, ...]:
    for token, identity in (
        ("yaml", "source-document"),
        ("schema", "source-document"),
        ("document", "source-document"),
        ("service", "service"),
        ("endpoint", "endpoint"),
        ("signal", "signal"),
        ("suite", "readiness-suite"),
        ("view", "operations-view"),
        ("secret", "secret-alias-or-binding"),
        ("provider", "provider-alias"),
        ("renderer", "renderer-binding"),
        ("dependency", "dependency"),
        ("host", "host"),
        ("waiver", "waiver"),
    ):
        if token in code:
            return (identity,)
    return ("observation-object",)


_CATALOG = {
    code: DiagnosticExplanation(
        code=code,
        meaning=f"The observation contract violates the {code.replace('-', ' ')} invariant.",
        affected_identity_types=_identity_types(code),
        likely_causes=("A source declaration is missing, malformed, duplicated, or inconsistent.",),
        next_actions=(
            "Use the diagnostic location and identity to repair the referenced declaration.",
        ),
    )
    for code in DIAGNOSTIC_CODES
}


def explain(code: str) -> DiagnosticExplanation:
    """Return stable repair guidance for a diagnostic code."""

    try:
        return _CATALOG[code]
    except KeyError:
        raise DiagnosticCodeNotFoundError(code) from None


__all__ = [
    "DIAGNOSTIC_CODES",
    "DiagnosticCodeNotFoundError",
    "DiagnosticExplanation",
    "explain",
]
