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
    FailurePolicy,
    HealthCapability,
    HealthEvaluator,
    Host,
    HostBaselineCapability,
    LogCapability,
    LogEvaluator,
    LogicalSignal,
    MetricCondition,
    MetricsCapability,
    MetricsEvaluator,
    ObservationBackend,
    OperationsView,
    OperationsViewSection,
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
    Waiver,
    WaiverScope,
    WaiverScopeKind,
)


@pytest.mark.parametrize(
    "host_id",
    [
        "11111111-1111-4111-8111-AAAAAAAAAAAA",
        "11111111111141118111111111111111",
        "{11111111-1111-4111-8111-111111111111}",
    ],
)
def test_host_id_rejects_noncanonical_uuid_spelling(host_id: str) -> None:
    with pytest.raises(ValidationError, match="canonical lowercase hyphenated"):
        Host(id=host_id)


def test_host_baseline_capabilities_are_closed_and_explicit() -> None:
    host = Host(
        id="11111111-1111-4111-8111-111111111111",
        baseline_capabilities=[
            HostBaselineCapability.DOCKER,
            HostBaselineCapability.CONTAINER_METRICS,
        ],
    )

    assert [item.value for item in host.baseline_capabilities] == ["docker", "container-metrics"]
    with pytest.raises(ValidationError):
        Host(id="11111111-1111-4111-8111-111111111111", baseline_capabilities=["ssh"])


def test_dependency_contract_defaults_to_gatus() -> None:
    dependency = DependencyContract(
        id="api-to-frontend",
        source_service_id="11111111-1111-4111-8111-111111111111/api",
        target_service_id="22222222-2222-4222-8222-222222222222/frontend",
        target_endpoint_id="22222222-2222-4222-8222-222222222222/frontend/http",
        protocol=EndpointProtocol.HTTP,
        port=8080,
        health_signal_ref="dependency/api-to-frontend/health/reachable",
    )

    assert dependency.execution_adapter == "gatus"


def test_dependency_contract_preserves_explicit_adapter_or_legacy_null() -> None:
    base = {
        "id": "api-to-frontend",
        "source_service_id": "11111111-1111-4111-8111-111111111111/api",
        "target_service_id": "22222222-2222-4222-8222-222222222222/frontend",
        "target_endpoint_id": "22222222-2222-4222-8222-222222222222/frontend/http",
        "protocol": EndpointProtocol.HTTP,
        "port": 8080,
        "health_signal_ref": "dependency/api-to-frontend/health/reachable",
    }

    assert (
        DependencyContract(**base, execution_adapter="edge-prober").execution_adapter
        == "edge-prober"
    )
    assert DependencyContract(**base, execution_adapter=None).execution_adapter is None


@pytest.mark.parametrize(
    ("model", "field"),
    [
        (Host, "baseline_capabilities"),
        (ServiceProfile, "required_host_baseline_capabilities"),
    ],
)
def test_baseline_capability_lists_reject_duplicates(model: type[object], field: str) -> None:
    kwargs: dict[str, object] = {
        field: [HostBaselineCapability.DOCKER, HostBaselineCapability.DOCKER]
    }
    if model is Host:
        kwargs["id"] = "11111111-1111-4111-8111-111111111111"
    else:
        kwargs["id"] = "nginx"

    with pytest.raises(ValidationError, match="duplicate host baseline capability"):
        model(**kwargs)  # type: ignore[call-arg]


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


def test_profile_rejects_duplicate_endpoint_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate endpoint id"):
        ServiceProfile(
            id="nginx",
            endpoints=[
                Endpoint(id="web", protocol=EndpointProtocol.HTTP, port=80),
                Endpoint(id="web", protocol=EndpointProtocol.HTTPS, port=443),
            ],
        )


def test_profile_rejects_duplicate_capability_ids_across_capability_kinds() -> None:
    with pytest.raises(ValidationError, match="duplicate capability id"):
        ServiceProfile(
            id="nginx",
            endpoints=[Endpoint(id="web", protocol=EndpointProtocol.HTTP, port=80)],
            health=[
                HealthCapability(
                    id="status", endpoint_id="web", evaluator=HealthEvaluator.HTTP_STATUS
                )
            ],
            metrics=[
                MetricsCapability(
                    id="status",
                    endpoint_id="web",
                    evaluator=MetricsEvaluator.PROMETHEUS_SCRAPE,
                )
            ],
        )


def test_profile_rejects_duplicate_signal_ids() -> None:
    signal = LogicalSignal(
        id="ready",
        capability_id="health",
        evaluator=SignalEvaluator.CAPABILITY_STATE,
    )
    with pytest.raises(ValidationError, match="duplicate signal id"):
        ServiceProfile(
            id="nginx",
            endpoints=[Endpoint(id="web", protocol=EndpointProtocol.HTTP, port=80)],
            health=[
                HealthCapability(
                    id="health", endpoint_id="web", evaluator=HealthEvaluator.HTTP_STATUS
                )
            ],
            signals=[signal, signal.model_copy()],
        )


def test_profile_rejects_signal_with_absent_capability() -> None:
    with pytest.raises(ValidationError, match="missing capability"):
        ServiceProfile(
            id="nginx",
            signals=[
                LogicalSignal(
                    id="ready",
                    capability_id="health",
                    evaluator=SignalEvaluator.CAPABILITY_STATE,
                )
            ],
        )


def test_profile_rejects_signal_resolving_to_wrong_capability_kind() -> None:
    with pytest.raises(ValidationError, match="metrics capability"):
        ServiceProfile(
            id="nginx",
            endpoints=[Endpoint(id="web", protocol=EndpointProtocol.HTTP, port=80)],
            health=[
                HealthCapability(
                    id="requests", endpoint_id="web", evaluator=HealthEvaluator.HTTP_STATUS
                )
            ],
            signals=[
                LogicalSignal(
                    id="request-rate-high",
                    capability_id="requests",
                    evaluator=SignalEvaluator.METRIC_THRESHOLD,
                    metric="requests_total",
                    condition=MetricCondition(operator=ComparisonOperator.GT, threshold=100),
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


@pytest.mark.parametrize("threshold", [float("nan"), float("inf"), float("-inf")])
def test_metric_condition_rejects_non_finite_thresholds(threshold: float) -> None:
    with pytest.raises(ValidationError):
        MetricCondition(operator=ComparisonOperator.GT, threshold=threshold)


def test_metric_condition_finite_threshold_is_serialization_safe() -> None:
    condition = MetricCondition(operator=ComparisonOperator.GTE, threshold=12.5)

    assert condition.model_dump_json() == '{"operator":"gte","threshold":12.5}'


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
    ],
)
def test_provider_metadata_rejects_inline_secret_material(metadata: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="inline secret"):
        ProviderAlias(
            id="database-password",
            provider="vault-main",
            project="production/mail",
            object_id="4af4a32d-f45e-46f7-ae19-2acc04df5419",
            metadata=metadata,
        )


def test_provider_metadata_allows_benign_secret_words_in_values() -> None:
    alias = ProviderAlias(
        id="database-password",
        provider="vault-main",
        project="production/mail",
        object_id="object/password",
        metadata={"description": "password token is rotated externally"},
    )

    assert alias.metadata["description"] == "password token is rotated externally"


def test_provider_fields_remain_opaque() -> None:
    binding = SecretBinding(
        id="db-password",
        slot_id="database-password",
        alias="database-password",
    )
    alias = ProviderAlias(
        id="database-password",
        provider="opaque://provider/kind",
        project="projects/production mail",
        object_id="4af4a32d-f45e-46f7-ae19-2acc04df5419/object/password",
        metadata={"description": "token used by the database password rotation job"},
    )

    assert binding.alias == "database-password"
    assert alias.provider == "opaque://provider/kind"
    assert alias.object_id.startswith("4af4a32d")


@pytest.mark.parametrize("field", ["password", "token", "value", "secret"])
def test_provider_alias_rejects_inline_secret_fields(field: str) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        ProviderAlias(
            id="database-password",
            provider="vault-main",
            project="production/mail",
            object_id="object/password",
            **{field: "cleartext"},
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
        source_service_id="00000000-0000-4000-8000-000000000001/nginx-1",
        target_service_id="00000000-0000-4000-8000-000000000002/postgres-1",
        target_endpoint_id="00000000-0000-4000-8000-000000000002/postgres-1/database",
        protocol=EndpointProtocol.POSTGRESQL,
        port=5432,
        required=True,
        health_signal_ref="dependency/web-to-database/health/database-ready",
    )

    assert instance.profile_id == "postgresql"
    assert dependency.target_service_id.endswith("/postgres-1")

    with pytest.raises(ValidationError):
        ServiceInstance(id="postgres-1", profile_id="PostgreSQL")
    with pytest.raises(ValidationError):
        DependencyContract(
            id="web-to-database",
            source_service_id="nginx-1",
            target_service_id="postgres-1",
            target_endpoint_id="postgres-1/database",
            protocol=EndpointProtocol.POSTGRESQL,
            port=5432,
            health_signal_ref="dependency/web-to-database/health/database-ready",
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
            endpoints=[Endpoint(id="database", protocol=EndpointProtocol.POSTGRESQL, port=5432)],
            health=[
                HealthCapability(
                    id="ready",
                    endpoint_id="database",
                    evaluator=HealthEvaluator.POSTGRES_READY,
                )
            ],
        ),
        ServiceProfile(
            id="redis",
            endpoints=[Endpoint(id="redis", protocol=EndpointProtocol.REDIS, port=6379)],
            health=[
                HealthCapability(
                    id="ready",
                    endpoint_id="redis",
                    evaluator=HealthEvaluator.REDIS_READY,
                )
            ],
            secret_slots=[
                SecretSlot(
                    id="password",
                    delivery_forms=[SecretDeliveryForm.ENVIRONMENT],
                    purpose="Redis authentication",
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
        "redis",
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
        purpose="Mail operations",
        sections=[
            OperationsViewSection(
                id="status",
                members=[
                    SignalMembership(
                        signal_id="smtp-ready",
                        signal_ref="smtp-ready",
                        datasource_binding_id="metrics-source",
                        requirement=SignalRequirement.REQUIRED,
                        display=SignalDisplay.STATUS,
                    )
                ],
            )
        ],
    )
    suite = ReadinessSuite(
        id="mail-readiness",
        members=[
            SuiteMember(
                id="smtp-ready",
                signal_ref="smtp-ready",
                cadence_seconds=30,
                continuity_seconds=300,
                freshness_seconds=90,
                no_data_policy=FailurePolicy.FAIL,
                error_policy=FailurePolicy.FAIL,
            )
        ],
    )

    assert view.sections[0].members[0].requirement.value == "required"
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
        observation_backend_id="metrics-primary",
        datasource_ref="uid/metrics",
        observed_at=datetime(2026, 8, 4, tzinfo=timezone.utc),
    )

    assert renderer.binding_ref == "ops/default"
    assert backend.backend_ref == "production/metrics"
    assert datasource.observation_backend_id == "metrics-primary"
