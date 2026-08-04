"""Tests for the public observation source contracts."""

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from infralink.observation.models import (
    Application,
    BackendKind,
    ComparisonOperator,
    DatasourceBinding,
    DependencyContract,
    Endpoint,
    EndpointProtocol,
    HealthCapability,
    HealthEvaluator,
    LogCapability,
    LogEvaluator,
    LogicalSignal,
    MetricCondition,
    MetricsCapability,
    MetricsEvaluator,
    ObservationBackend,
    OperationsView,
    ProviderAlias,
    ReadinessSuite,
    RendererBindingIdentity,
    SecretBinding,
    SecretDeliveryForm,
    SecretSlot,
    ServiceInstance,
    ServiceProfile,
    SignalDisplay,
    SignalEvaluator,
    SignalMembership,
    SignalRequirement,
    SuiteMember,
    SuitePolicy,
    Waiver,
    WaiverScope,
    WaiverScopeKind,
)


def test_profile_binds_capabilities_to_named_endpoints() -> None:
    profile = ServiceProfile(
        id="postgresql",
        endpoints=[Endpoint(id="database", protocol=EndpointProtocol.POSTGRESQL, port=5432)],
        health=[
            HealthCapability(
                id="ready",
                endpoint_id="database",
                evaluator=HealthEvaluator.POSTGRES_READY,
            )
        ],
        metrics=[
            MetricsCapability(
                id="metrics",
                endpoint_id="database",
                evaluator=MetricsEvaluator.PROMETHEUS_SCRAPE,
            )
        ],
    )

    assert profile.health[0].endpoint_id == "database"


def test_profile_rejects_capability_with_absent_endpoint() -> None:
    with pytest.raises(ValidationError, match="missing endpoint"):
        ServiceProfile(
            id="nginx",
            endpoints=[Endpoint(id="web", protocol=EndpointProtocol.HTTP, port=80)],
            health=[
                HealthCapability(
                    id="ready",
                    endpoint_id="admin",
                    evaluator=HealthEvaluator.HTTP_STATUS,
                )
            ],
        )


def test_metric_logical_signal_is_vendor_neutral() -> None:
    signal = LogicalSignal(
        id="mail-queue-high",
        capability_id="mail-metrics",
        evaluator=SignalEvaluator.METRIC_THRESHOLD,
        metric="mail_queue_depth",
        condition=MetricCondition(operator=ComparisonOperator.GT, threshold=100),
    )

    assert signal.condition is not None
    assert signal.condition.operator.value == "gt"


@pytest.mark.parametrize("bad_id", ["Upper", "two_words", "-leading", "trailing-", ""])
def test_canonical_ids_are_not_normalized(bad_id: str) -> None:
    with pytest.raises(ValidationError):
        Endpoint(id=bad_id, protocol=EndpointProtocol.TCP, port=25)


def test_models_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        Endpoint(id="smtp", protocol=EndpointProtocol.SMTP, port=25, vendor="postfix")


@pytest.mark.parametrize("port", [True, 0, 65536])
def test_endpoints_reject_invalid_ports(port: object) -> None:
    with pytest.raises(ValidationError):
        Endpoint(id="smtp", protocol=EndpointProtocol.SMTP, port=port)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"protocol": "tcp", "port": 25},
        {"protocol": EndpointProtocol.TCP, "port": "25"},
    ],
)
def test_strict_models_reject_coercion(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Endpoint(id="smtp", **kwargs)  # type: ignore[arg-type]


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
        delivery_forms=[SecretDeliveryForm.ENVIRONMENT, SecretDeliveryForm.FILE],
        purpose="Authenticate to PostgreSQL",
    )
    optional = SecretSlot(
        id="tls-certificate",
        required=False,
        delivery_forms=[SecretDeliveryForm.FILE],
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
            provider_ref="object-id",
            metadata=metadata,
        )


def test_provider_fields_remain_opaque() -> None:
    binding = SecretBinding(
        id="db-password",
        slot_id="database-password",
        provider="company-store",
        provider_ref="object-7f21",
        metadata={"mount": "production"},
    )
    alias = ProviderAlias(id="company-store", provider="opaque-provider-kind")

    assert binding.provider_ref == "object-7f21"
    assert alias.provider == "opaque-provider-kind"


@pytest.mark.parametrize("provider_ref", ["password-value", "api-token", "secret"])
def test_provider_reference_rejects_secret_like_identifiers(provider_ref: str) -> None:
    with pytest.raises(ValidationError, match="secret-like"):
        SecretBinding(
            id="db-password",
            slot_id="database-password",
            provider="company-store",
            provider_ref=provider_ref,
        )


def test_service_instance_and_dependency_contract_are_strict_contracts() -> None:
    instance = ServiceInstance(
        id="postgres-1",
        profile_id="postgresql",
        endpoint_ids=["database"],
        secret_binding_ids=["database-credentials"],
    )
    dependency = DependencyContract(
        id="web-to-database",
        source_service_id="nginx-1",
        target_service_id="postgres-1",
        required=True,
        health_signal_refs=["database-ready"],
    )

    assert instance.profile_id == "postgresql"
    assert dependency.target_service_id == "postgres-1"

    with pytest.raises(ValidationError):
        ServiceInstance(id="postgres-1", profile_id="PostgreSQL")
    with pytest.raises(ValidationError):
        DependencyContract(
            id="web-to-database",
            source_service_id="nginx-1",
            target_service_id="postgres-1",
            required="yes",
        )


def test_representative_profiles_use_only_closed_vendor_neutral_contracts() -> None:
    profiles = [
        ServiceProfile(
            id="nginx",
            endpoints=[Endpoint(id="web", protocol=EndpointProtocol.HTTP, port=80)],
            health=[
                HealthCapability(
                    id="ready", endpoint_id="web", evaluator=HealthEvaluator.HTTP_STATUS
                )
            ],
        ),
        ServiceProfile(
            id="postfix",
            endpoints=[Endpoint(id="smtp", protocol=EndpointProtocol.SMTP, port=25)],
            health=[
                HealthCapability(
                    id="ready", endpoint_id="smtp", evaluator=HealthEvaluator.SMTP_BANNER
                )
            ],
        ),
        ServiceProfile(
            id="inspircd",
            endpoints=[Endpoint(id="irc", protocol=EndpointProtocol.IRC, port=6667)],
            health=[
                HealthCapability(
                    id="ready", endpoint_id="irc", evaluator=HealthEvaluator.IRC_HANDSHAKE
                )
            ],
        ),
        ServiceProfile(
            id="postgresql",
            endpoints=[
                Endpoint(id="database", protocol=EndpointProtocol.POSTGRESQL, port=5432)
            ],
            health=[
                HealthCapability(
                    id="ready",
                    endpoint_id="database",
                    evaluator=HealthEvaluator.POSTGRES_READY,
                )
            ],
        ),
        ServiceProfile(
            id="ci",
            endpoints=[Endpoint(id="agent", protocol=EndpointProtocol.TCP, port=3000)],
            metrics=[
                MetricsCapability(
                    id="metrics",
                    endpoint_id="agent",
                    evaluator=MetricsEvaluator.PROMETHEUS_SCRAPE,
                )
            ],
            logs=[LogCapability(id="errors", evaluator=LogEvaluator.REGEX)],
        ),
    ]

    assert [profile.id for profile in profiles] == [
        "nginx",
        "postfix",
        "inspircd",
        "postgresql",
        "ci",
    ]


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
            SignalMembership(
                signal_ref="smtp-ready",
                requirement=SignalRequirement.REQUIRED,
                display=SignalDisplay.STATUS,
            ),
            SignalMembership(
                signal_ref="queue",
                requirement=SignalRequirement.OPTIONAL,
                display=SignalDisplay.VALUE,
            ),
        ],
    )
    suite = ReadinessSuite(
        id="mail-readiness",
        members=[
            SuiteMember(
                signal_ref="smtp-ready",
                policy=SuitePolicy.MUST_PASS,
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
            scope=WaiverScope(
                kind=WaiverScopeKind.SUITE_MEMBER,
                ref="smtp-ready",
                suite_ref="mail-readiness",
            ),
            owner="platform-team",
            reason="Migration window",
            created_on=date(2026, 8, 4),
            expires_on=date(2026, 8, 4),
        )


def test_waiver_carries_stable_audit_fields() -> None:
    waiver = Waiver(
        id="temporary-mail-waiver",
        scope=WaiverScope(
            kind=WaiverScopeKind.SUITE_MEMBER,
            ref="smtp-ready",
            suite_ref="mail-readiness",
        ),
        owner="platform-team",
        reason="Migration window",
        created_on=date(2026, 8, 4),
        expires_on=date(2026, 8, 11),
    )
    assert waiver.expires_on > waiver.created_on


@pytest.mark.parametrize("kind", list(WaiverScopeKind))
def test_waiver_scope_targets_a_closed_kind_and_canonical_reference(
    kind: WaiverScopeKind,
) -> None:
    suite_ref = "mail-readiness" if kind == WaiverScopeKind.SUITE_MEMBER else None
    scope = WaiverScope(kind=kind, ref="smtp-ready", suite_ref=suite_ref)
    assert scope.ref == "smtp-ready"


def test_waiver_scope_rejects_unknown_kind_and_invalid_reference() -> None:
    with pytest.raises(ValidationError):
        WaiverScope(kind="dashboard", ref="smtp-ready")
    with pytest.raises(ValidationError):
        WaiverScope(kind=WaiverScopeKind.SIGNAL, ref="SMTP Ready")
    with pytest.raises(ValidationError, match="suite_ref"):
        WaiverScope(kind=WaiverScopeKind.SUITE_MEMBER, ref="smtp-ready")
    with pytest.raises(ValidationError, match="suite_ref"):
        WaiverScope(
            kind=WaiverScopeKind.CAPABILITY,
            ref="smtp-ready",
            suite_ref="mail-readiness",
        )


def test_opaque_binding_models_capture_identity_without_vendor_config() -> None:
    renderer = RendererBindingIdentity(
        id="grafana-primary", renderer="dashboard", binding_ref="ops/default"
    )
    backend = ObservationBackend(
        id="metrics-primary", kind=BackendKind.METRICS, backend_ref="production/metrics"
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
