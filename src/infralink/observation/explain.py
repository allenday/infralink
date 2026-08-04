"""Stable explanations for observation diagnostics."""

from __future__ import annotations

from dataclasses import asdict, dataclass

_LOADER_CODES = (
    "canonical-value-unsupported",
    "document-root-not-mapping",
    "duplicate-object-id",
    "mapping-key-not-string",
    "schema-version-missing",
    "schema-version-unsupported",
    "source-not-found",
    "unsupported-source-extension",
    "yaml-alias-forbidden",
    "yaml-malformed",
    "yaml-nesting-too-deep",
    "yaml-source-too-complex",
    "yaml-source-too-large",
    "yaml-too-many-documents",
)

_PLANNER_CODES = (
    "dependency-port-conflict",
    "dependency-protocol-conflict",
    "dependency-target-mismatch",
    "duplicate-application-id",
    "duplicate-datasource-binding-id",
    "duplicate-dependency-id",
    "duplicate-endpoint-override",
    "duplicate-host-id",
    "duplicate-observation-backend-id",
    "duplicate-profile-id",
    "duplicate-provider-alias-id",
    "duplicate-renderer-binding-id",
    "duplicate-secret-binding-id",
    "duplicate-service-id",
    "duplicate-signal-id",
    "duplicate-suite-id",
    "duplicate-suite-member-id",
    "duplicate-view-id",
    "duplicate-view-query-id",
    "duplicate-view-section-id",
    "duplicate-waiver-id",
    "endpoint-override-not-selected",
    "invalid-dependency-health-signal-ref",
    "invalid-document-record",
    "invalid-document-section",
    "missing-service-host",
    "no-usable-observation-document",
    "optional-view-signal-gate",
    "registry-revision-conflict",
    "renderer-delivery-incompatible",
    "required-secret-slot-unbound",
    "secret-delivery-incompatible",
    "unknown-application-dependency",
    "unknown-application-health-signal",
    "unknown-application-service",
    "unknown-dependency-source",
    "unknown-dependency-target",
    "unknown-document-field",
    "unknown-endpoint",
    "unknown-endpoint-override",
    "unknown-host",
    "unknown-observation-backend",
    "unknown-profile",
    "unknown-provider-alias",
    "unknown-renderer-binding",
    "unknown-secret-binding",
    "unknown-secret-slot",
    "unknown-selected-endpoint",
    "unknown-suite-signal",
    "unknown-view-datasource-binding",
    "unknown-view-signal",
    "unknown-waiver-target",
    "view-datasource-kind-incompatible",
    "view-signal-ref-kind",
    "waiver-expired",
)

DIAGNOSTIC_CODES = tuple(sorted(set(_LOADER_CODES + _PLANNER_CODES)))


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
