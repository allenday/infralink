"""Tests for the public observation source contracts."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from infralink.observation.models import (
    Application,
    DatasourceBinding,
    Endpoint,
    HealthCapability,
    LogCapability,
    LogicalSignal,
    MetricCondition,
    MetricsCapability,
    ObservationBackend,
    OperationsView,
    ProviderAlias,
    ReadinessSuite,
    RendererBindingIdentity,
    SecretBinding,
    SecretSlot,
    ServiceProfile,
    SignalMembership,
    SuiteMember,
    Waiver,
)


def test_profile_binds_capabilities_to_named_endpoints() -> None:
    profile = ServiceProfile(
        id="postgresql",
        endpoints=[Endpoint(id="database", protocol="postgresql", port=5432)],
        health=[
            HealthCapability(
                id="ready", endpoint_id="database", evaluator="postgres-ready"
            )
        ],
        metrics=[
            MetricsCapability(
                id="metrics", endpoint_id="database", evaluator="prometheus-scrape"
            )
        ],
    )

    assert profile.health[0].endpoint_id == "database"


def test_profile_rejects_capability_with_absent_endpoint() -> None:
    with pytest.raises(ValidationError, match="missing endpoint"):
        ServiceProfile(
            id="nginx",
            endpoints=[Endpoint(id="web", protocol="http", port=80)],
            health=[HealthCapability(id="ready", endpoint_id="admin", evaluator="http-status")],
        )


def test_metric_logical_signal_is_vendor_neutral() -> None:
    signal = LogicalSignal(
        id="mail-queue-high",
        capability_id="mail-metrics",
        evaluator="metric-threshold",
        metric="mail_queue_depth",
        condition=MetricCondition(operator="gt", threshold=100),
    )

    assert signal.condition is not None
    assert signal.condition.operator.value == "gt"


@pytest.mark.parametrize("bad_id", ["Upper", "two_words", "-leading", "trailing-", ""])
def test_canonical_ids_are_not_normalized(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Endpoint(id=bad_id, protocol="tcp", port=25)


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Endpoint(id="smtp", protocol="smtp", port=25, vendor="postfix")


@pytest.mark.parametrize("port", [True, 0, 65536])
def test_endpoints_reject_invalid_ports(port: object) -> None:
    with pytest.raises(ValidationError):
        Endpoint(id="smtp", protocol="smtp", port=port)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("model", "kwargs"),
    [
        (HealthCapability, {"endpoint_id": "web", "evaluator": "curl"}),
        (MetricsCapability, {"endpoint_id": "web", "evaluator": "datadog"}),
        (LogCapability, {"evaluator": "splunk-query"}),
        (LogicalSignal, {"capability_id": "health", "evaluator": "python"}),
    ],
)
def test_unknown_evaluators_fail_closed(model: type[object], kwargs: dict[str, str]) -> None:
    with pytest.raises(ValidationError):
        model(id="signal", **kwargs)  # type: ignore[call-arg]


def test_secret_slots_express_requirement_delivery_and_purpose() -> None:
    required = SecretSlot(
        id="database-password",
        required=True,
        delivery_forms=["environment", "file"],
        purpose="Authenticate to PostgreSQL",
    )
    optional = SecretSlot(
        id="tls-certificate",
        required=False,
        delivery_forms=["file"],
        purpose="Serve TLS",
    )

    assert required.required is True
    assert optional.required is False


def test_secret_slot_requires_at_least_one_delivery_form() -> None:
    with pytest.raises(ValidationError):
        SecretSlot(id="password", delivery_forms=[], purpose="Authenticate")


@pytest.mark.parametrize(
    "metadata",
    [
        {"password": "cleartext"},
        {"apiToken": "cleartext"},
        {"nested": {"secret_value": "cleartext"}},
        {"setting": "password=hunter2"},
    ],
)
def test_provider_metadata_rejects_inline_secret_material(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="inline secret"):
        SecretBinding(
            id="db-password",
            slot_id="database-password",
            provider="vault-main",
            provider_ref="opaque/object/id",
            metadata=metadata,
        )


def test_provider_fields_remain_opaque() -> None:
    binding = SecretBinding(
        id="db-password",
        slot_id="database-password",
        provider="company-store",
        provider_ref="opaque://anything provider accepts",
        metadata={"mount": "production"},
    )
    alias = ProviderAlias(id="company-store", provider="opaque-provider-kind")

    assert binding.provider_ref == "opaque://anything provider accepts"
    assert alias.provider == "opaque-provider-kind"


def test_application_members_edges_and_health_are_explicit() -> None:
    application = Application(
        id="mail",
        service_instance_ids=["smtp-1", "imap-1"],
        required_dependency_edge_ids=["smtp-to-database"],
        health_signal_refs=["smtp-ready", "mail-queue-high"],
    )

    assert application.service_instance_ids == ["smtp-1", "imap-1"]
    assert application.required_dependency_edge_ids == ["smtp-to-database"]


def test_views_and_suites_have_typed_membership_policy() -> None:
    view = OperationsView(
        id="mail-operations",
        title="Mail operations",
        signals=[
            SignalMembership(signal_ref="smtp-ready", requirement="required", display="status"),
            SignalMembership(signal_ref="queue", requirement="optional", display="value"),
        ],
    )
    suite = ReadinessSuite(
        id="mail-readiness",
        members=[
            SuiteMember(
                signal_ref="smtp-ready",
                policy="must-pass",
                cadence_seconds=30,
                continuity_seconds=300,
                freshness_seconds=90,
            )
        ],
    )

    assert view.signals[0].requirement.value == "required"
    assert suite.members[0].continuity_seconds == 300


def test_waiver_requires_expiry_after_creation() -> None:
    with pytest.raises(ValidationError, match="expiry"):
        Waiver(
            id="temporary-mail-waiver",
            scope="suite:mail-readiness/member:smtp-ready",
            owner="platform-team",
            reason="Migration window",
            created_on=date(2026, 8, 4),
            expires_on=date(2026, 8, 4),
        )


def test_waiver_carries_stable_audit_fields() -> None:
    waiver = Waiver(
        id="temporary-mail-waiver",
        scope="suite:mail-readiness/member:smtp-ready",
        owner="platform-team",
        reason="Migration window",
        created_on=date(2026, 8, 4),
        expires_on=date(2026, 8, 11),
    )
    assert waiver.expires_on > waiver.created_on


def test_opaque_binding_models_capture_identity_without_vendor_config() -> None:
    renderer = RendererBindingIdentity(
        id="grafana-primary", renderer="dashboard", binding_ref="ops/default"
    )
    backend = ObservationBackend(
        id="metrics-primary", kind="metrics", backend_ref="production/metrics"
    )
    datasource = DatasourceBinding(
        id="metrics-source",
        backend_id="metrics-primary",
        datasource_ref="uid/metrics",
        observed_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert renderer.binding_ref == "ops/default"
    assert backend.backend_ref == "production/metrics"
    assert datasource.backend_id == "metrics-primary"
