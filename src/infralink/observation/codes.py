"""Dependency-neutral registry of diagnostics emitted by observation processing."""

LOADER_DIAGNOSTIC_CODES = frozenset(
    {
        "component-resource-binding-unknown-slot",
        "component-resource-required-unbound",
        "external-service-resource-unknown-contract",
        "external-service-resource-binding-mismatch",
        "secret-resource-binding-invalid-reference",
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
        "logical-service",
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
        "logical-service-component-signal-unknown",
        "logical-service-component-unresolved",
        "missing-service-host",
        "missing-required-host-baseline-capability",
        "host-metrics-profile-capability-required",
        "nonhost-host-bridge-ingress-service",
        "nonlocal-host-bridge-ingress-service",
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
        "unknown-host-bridge-ingress-service",
        "unknown-host-metrics-profile",
        "profile-metrics-profile-capability-required",
        "unknown-profile-metrics-profile",
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

V2_DIAGNOSTIC_CODES = frozenset(
    {
        "component-endpoint-binding-unknown-endpoint",
        "component-edge-incompatible-protocol",
        "component-edge-unknown-endpoint",
        "component-metric-binding-label-not-allowed",
        "component-metric-binding-unknown-contract",
        "component-metric-source-endpoint-unbound",
        "duplicate-component-edge-id",
        "duplicate-component-edge-semantics",
        "no-usable-v2-metric-document",
        "mixed-observation-schema-versions",
        "service-instance-unknown-component-slot",
        "service-instance-missing-component-slot",
        "service-instance-unknown-profile",
        "v2-component-topology-invalid",
        "v2-metric-source-version-invalid",
        "v2-observation-source-version-invalid",
        "v2-registry-revision-unsupported",
    }
)

PUBLIC_API_DIAGNOSTIC_CODES = frozenset({"invalid-as-of", "invalid-registry-revision"})

ALL_DIAGNOSTIC_CODES = (
    LOADER_DIAGNOSTIC_CODES
    | PLANNER_DIAGNOSTIC_CODES
    | V2_DIAGNOSTIC_CODES
    | PUBLIC_API_DIAGNOSTIC_CODES
)

__all__ = [
    "ALL_DIAGNOSTIC_CODES",
    "DUPLICATE_IDENTITY_KINDS",
    "LOADER_DIAGNOSTIC_CODES",
    "PLANNER_DIAGNOSTIC_CODES",
    "PUBLIC_API_DIAGNOSTIC_CODES",
    "V2_DIAGNOSTIC_CODES",
]
