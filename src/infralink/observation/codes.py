"""Dependency-neutral registry of diagnostics emitted by observation processing."""

LOADER_DIAGNOSTIC_CODES = frozenset(
    {
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
    }
)

DUPLICATE_IDENTITY_KINDS = frozenset(
    {
        "application",
        "datasource-binding",
        "dependency",
        "endpoint",
        "host",
        "observation-backend",
        "profile",
        "provider-alias",
        "renderer-binding",
        "secret-binding",
        "signal",
        "suite",
        "view",
        "waiver",
    }
)

_DYNAMIC_DUPLICATE_CODES = frozenset(f"duplicate-{kind}-id" for kind in DUPLICATE_IDENTITY_KINDS)

PLANNER_DIAGNOSTIC_CODES = _DYNAMIC_DUPLICATE_CODES | frozenset(
    {
        "dependency-port-conflict",
        "dependency-protocol-conflict",
        "dependency-target-mismatch",
        "capability-endpoint-not-selected",
        "duplicate-endpoint-override",
        "duplicate-service-id",
        "duplicate-suite-member-id",
        "duplicate-view-query-id",
        "duplicate-view-section-id",
        "endpoint-override-not-selected",
        "invalid-dependency-health-signal-ref",
        "invalid-document-record",
        "invalid-document-section",
        "missing-service-host",
        "missing-required-host-baseline-capability",
        "host-metrics-profile-capability-required",
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
        "unknown-host-metrics-profile",
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
    }
)

PUBLIC_API_DIAGNOSTIC_CODES = frozenset({"invalid-as-of", "invalid-registry-revision"})

ALL_DIAGNOSTIC_CODES = (
    LOADER_DIAGNOSTIC_CODES | PLANNER_DIAGNOSTIC_CODES | PUBLIC_API_DIAGNOSTIC_CODES
)

__all__ = [
    "ALL_DIAGNOSTIC_CODES",
    "DUPLICATE_IDENTITY_KINDS",
    "LOADER_DIAGNOSTIC_CODES",
    "PLANNER_DIAGNOSTIC_CODES",
    "PUBLIC_API_DIAGNOSTIC_CODES",
]
