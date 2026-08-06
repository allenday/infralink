from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from infralink.observation.loader import ObservationDocument
from infralink.observation.planner import Plan, PlanValidationError, resolve_observation_documents

AS_OF = datetime(2026, 8, 4, tzinfo=timezone.utc)


def document(data: dict[str, object], path: str = "contract.yml") -> ObservationDocument:
    return ObservationDocument(path, MappingProxyType(data), "raw", path, 0)


def base_data() -> dict[str, object]:
    return {
        "schema_version": "infralink.observation/v1",
        "registry_revision": "registry-7",
        "service_profiles": [
            {
                "id": "web",
                "endpoints": [{"id": "http", "protocol": "http", "port": 8080}],
                "health": [{"id": "ready", "endpoint_id": "http", "evaluator": "http-status"}],
                "signals": [
                    {"id": "up", "capability_id": "ready", "evaluator": "capability-state"}
                ],
                "secret_slots": [
                    {
                        "id": "password",
                        "required": True,
                        "delivery_forms": ["file"],
                        "purpose": "authentication",
                    }
                ],
            }
        ],
        "hosts": [
            {"id": "11111111-1111-4111-8111-111111111111"},
            {"id": "22222222-2222-4222-8222-222222222222"},
        ],
        "service_instances": [
            {
                "id": "frontend",
                "host_id": "22222222-2222-4222-8222-222222222222",
                "profile_id": "web",
                "secret_binding_ids": ["frontend-password"],
            },
            {
                "id": "api",
                "host_id": "11111111-1111-4111-8111-111111111111",
                "profile_id": "web",
                "secret_binding_ids": ["api-password"],
            },
        ],
        "provider_aliases": [
            {"id": "shared-password", "provider": "vault", "project": "prod", "object_id": "x"}
        ],
        "secret_bindings": [
            {
                "id": "api-password",
                "slot_id": "password",
                "alias": "shared-password",
                "delivery": "file",
            },
            {
                "id": "frontend-password",
                "slot_id": "password",
                "alias": "shared-password",
                "delivery": "file",
            },
        ],
        "dependency_contracts": [
            {
                "id": "api-to-frontend",
                "source_service_id": "11111111-1111-4111-8111-111111111111/api",
                "target_service_id": "22222222-2222-4222-8222-222222222222/frontend",
                "target_endpoint_id": "22222222-2222-4222-8222-222222222222/frontend/http",
                "protocol": "http",
                "port": 8080,
                "health_signal_ref": "dependency/api-to-frontend/health/reachable",
            }
        ],
        "applications": [
            {
                "id": "site",
                "service_instance_ids": ["11111111-1111-4111-8111-111111111111/api"],
                "required_dependency_edge_ids": ["api-to-frontend"],
                "health_signal_refs": ["service/11111111-1111-4111-8111-111111111111/api/ready/up"],
            }
        ],
    }


def test_resolves_two_hosts_and_exact_signal_namespaces_deterministically() -> None:
    plan = resolve_observation_documents([document(base_data())], as_of=AS_OF)

    assert [host.id for host in plan.hosts] == [
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    ]
    assert [service.id for service in plan.services] == [
        "11111111-1111-4111-8111-111111111111/api",
        "22222222-2222-4222-8222-222222222222/frontend",
    ]
    assert [signal.id for signal in plan.signals] == [
        "dependency/api-to-frontend/health/reachable",
        "service/11111111-1111-4111-8111-111111111111/api/ready/up",
        "service/22222222-2222-4222-8222-222222222222/frontend/ready/up",
    ]
    assert plan.dependencies[0].health_signal_refs == (
        "dependency/api-to-frontend/health/reachable",
    )
    assert plan.schema_version == "infralink.plan.v1"
    assert plan.document_digests == ("contract.yml",)


def test_projects_explicit_host_and_service_display_names_without_identity_changes() -> None:
    data = base_data()
    data["hosts"][0]["display_name"] = "Operations API"  # type: ignore[index]
    data["hosts"][1]["display_name"] = "Customer Edge"  # type: ignore[index]
    data["service_profiles"][0]["display_name"] = "Web Service"  # type: ignore[index]
    data["service_instances"][0]["display_name"] = "Public Frontend"  # type: ignore[index]

    plan = resolve_observation_documents([document(data)], as_of=AS_OF)

    assert [(host.id, host.display_name) for host in plan.hosts] == [
        ("11111111-1111-4111-8111-111111111111", "Operations API"),
        ("22222222-2222-4222-8222-222222222222", "Customer Edge"),
    ]
    assert [(service.id, service.display_name) for service in plan.services] == [
        ("11111111-1111-4111-8111-111111111111/api", "Web Service"),
        ("22222222-2222-4222-8222-222222222222/frontend", "Public Frontend"),
    ]
    assert resolve_observation_documents([document(data)], as_of=AS_OF) == plan


def test_legacy_source_documents_project_no_display_names() -> None:
    plan = resolve_observation_documents([document(base_data())], as_of=AS_OF)

    assert [host.display_name for host in plan.hosts] == [None, None]
    assert [service.display_name for service in plan.services] == [None, None]


def test_legacy_v1_plan_payload_without_display_names_still_validates() -> None:
    payload = resolve_observation_documents([document(base_data())], as_of=AS_OF).model_dump()
    for host in payload["hosts"]:
        host.pop("display_name")
    for service in payload["services"]:
        service.pop("display_name")

    restored = Plan.model_validate(payload)

    assert restored.schema_version == "infralink.plan.v1"
    assert [host.display_name for host in restored.hosts] == [None, None]
    assert [service.display_name for service in restored.services] == [None, None]


def test_instance_applies_only_address_exposure_and_route_overrides() -> None:
    data = base_data()
    data["service_instances"][0]["endpoint_overrides"] = [  # type: ignore[index]
        {
            "endpoint_id": "http",
            "address": "frontend.internal",
            "exposure": "private",
            "route": "/healthz",
        }
    ]
    plan = resolve_observation_documents([document(data)], as_of=AS_OF)
    endpoint = next(item for item in plan.endpoints if "/frontend/http" in item.id)
    assert (endpoint.address, endpoint.exposure, endpoint.path, endpoint.port) == (
        "frontend.internal",
        "private",
        "/healthz",
        8080,
    )


def test_dependency_accepts_explicit_namespaced_health_signal_ref() -> None:
    data = base_data()
    plan = resolve_observation_documents([document(data)], as_of=AS_OF)
    assert plan.dependencies[0].health_signal_refs == (
        "dependency/api-to-frontend/health/reachable",
    )


@pytest.mark.parametrize("capability_id", ["ready", "metrics"])
def test_signal_capability_endpoint_must_be_selected(capability_id: str) -> None:
    data = base_data()
    profile = data["service_profiles"][0]  # type: ignore[index]
    profile["endpoints"].append({"id": "unused", "protocol": "http", "port": 8081})
    profile["metrics"] = [
        {"id": "metrics", "endpoint_id": "http", "evaluator": "prometheus-scrape"}
    ]
    profile["signals"].append(
        {
            "id": "load",
            "capability_id": "metrics",
            "evaluator": "metric-threshold",
            "metric": "queue_depth",
            "condition": {"operator": "lt", "threshold": 10},
        }
    )
    for instance in data["service_instances"]:  # type: ignore[assignment]
        instance["endpoint_ids"] = ["unused"]

    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)

    findings = [
        item
        for item in caught.value.report.diagnostics
        if item.code == "capability-endpoint-not-selected"
    ]
    assert any(item.identity and capability_id in item.identity for item in findings)
    assert all(
        item.location.pointer.startswith("/service_profiles/0/signals/") for item in findings
    )


def test_planned_signals_serialize_adapter_inputs_and_digest_changes() -> None:
    data = base_data()
    profile = data["service_profiles"][0]  # type: ignore[index]
    profile["metrics"] = [
        {"id": "metrics", "endpoint_id": "http", "evaluator": "prometheus-scrape"}
    ]
    profile["signals"].append(
        {
            "id": "load",
            "capability_id": "metrics",
            "evaluator": "metric-threshold",
            "metric": "queue_depth",
            "condition": {"operator": "lt", "threshold": 10},
        }
    )
    first = resolve_observation_documents([document(data)], as_of=AS_OF)
    metric = next(signal for signal in first.signals if signal.id.endswith("/metrics/load"))
    dependency = next(signal for signal in first.signals if signal.kind == "dependency")

    assert metric.source_endpoint_id.endswith("/http")
    assert (metric.metric, metric.comparator, metric.threshold) == ("queue_depth", "lt", 10)
    assert metric.capability_evaluator == "prometheus-scrape"
    assert metric.capability_path is None
    assert metric.log_stream is None
    assert dependency.source_endpoint_id.endswith("/frontend/http")
    assert dependency.capability_evaluator == "dependency-health"

    profile["signals"][-1]["condition"]["threshold"] = 11
    second = resolve_observation_documents([document(data)], as_of=AS_OF)
    assert first.plan_digest != second.plan_digest


def test_capability_path_and_log_stream_are_normalized_and_digest_relevant() -> None:
    data = base_data()
    profile = data["service_profiles"][0]  # type: ignore[index]
    profile["health"][0]["path"] = "/healthz"
    profile["metrics"] = [
        {
            "id": "metrics",
            "endpoint_id": "http",
            "evaluator": "prometheus-scrape",
            "path": "/metrics",
        }
    ]
    profile["logs"] = [{"id": "access", "evaluator": "contains", "stream": "nginx.access"}]
    profile["signals"].extend(
        [
            {
                "id": "load",
                "capability_id": "metrics",
                "evaluator": "metric-threshold",
                "metric": "requests_total",
                "condition": {"operator": "gte", "threshold": 0},
            },
            {
                "id": "traffic",
                "capability_id": "access",
                "evaluator": "log-match",
                "pattern": "GET",
            },
        ]
    )
    first = resolve_observation_documents([document(data)], as_of=AS_OF)

    assert (
        next(item for item in first.signals if item.id.endswith("/ready/up")).capability_path
        == "/healthz"
    )
    assert (
        next(item for item in first.signals if item.id.endswith("/metrics/load")).capability_path
        == "/metrics"
    )
    assert (
        next(item for item in first.signals if item.id.endswith("/access/traffic")).log_stream
        == "nginx.access"
    )

    profile["logs"][0]["stream"] = "nginx.changed"
    second = resolve_observation_documents([document(data)], as_of=AS_OF)
    assert first.plan_digest != second.plan_digest


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda d: d["service_instances"][0].update(profile_id="missing"), "unknown-profile"),
        (
            lambda d: d["dependency_contracts"][0].update(
                target_endpoint_id="missing/service/http"
            ),
            "unknown-endpoint",
        ),
        (
            lambda d: d["dependency_contracts"][0].update(protocol="https"),
            "dependency-protocol-conflict",
        ),
        (lambda d: d["dependency_contracts"][0].update(port=80), "dependency-port-conflict"),
        (lambda d: d["secret_bindings"].pop(), "required-secret-slot-unbound"),
        (lambda d: d["secret_bindings"][0].update(slot_id="missing"), "unknown-secret-slot"),
        (lambda d: d["secret_bindings"][0].update(alias="missing"), "unknown-provider-alias"),
        (
            lambda d: d["secret_bindings"][0].update(delivery="environment"),
            "secret-delivery-incompatible",
        ),
    ],
)
def test_cross_reference_errors_are_typed(mutate: object, code: str) -> None:
    data = base_data()
    mutate(data)  # type: ignore[operator]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert code in {item.code for item in caught.value.report.diagnostics}
    assert all(item.identity and item.next_actions for item in caught.value.report.diagnostics)


def test_aggregates_independent_errors_and_does_not_infer_service_names() -> None:
    data = base_data()
    data["service_instances"][0]["profile_id"] = "missing"  # type: ignore[index]
    data["applications"][0]["service_instance_ids"] = ["api"]  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert {item.code for item in caught.value.report.diagnostics} >= {
        "unknown-profile",
        "unknown-application-service",
    }


def test_expired_waiver_and_unknown_target_are_reported() -> None:
    data = base_data()
    data["waivers"] = [
        {
            "id": "old",
            "scope": {"kind": "signal", "ref": "service/missing/x/y/z"},
            "owner": "ops",
            "reason": "migration",
            "created_on": "2026-01-01",
            "expires_on": "2026-08-03",
        }
    ]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert {item.code for item in caught.value.report.diagnostics} >= {
        "waiver-expired",
        "unknown-waiver-target",
    }


@pytest.mark.parametrize(
    "documents",
    [
        [],
        [document({"schema_version": "infralink.observation/v2"})],
        [document({"schema_version": "infralink.observation/v1", "hosts": []})],
    ],
)
def test_requires_a_usable_exact_version_document(
    documents: list[ObservationDocument],
) -> None:
    with pytest.raises(PlanValidationError):
        resolve_observation_documents(documents, as_of=AS_OF)


def test_same_instance_key_on_different_hosts_is_allowed_but_same_canonical_id_is_not() -> None:
    data = base_data()
    data["service_instances"][0]["id"] = "api"  # type: ignore[index]
    edge = data["dependency_contracts"][0]  # type: ignore[index]
    edge["target_service_id"] = "22222222-2222-4222-8222-222222222222/api"
    edge["target_endpoint_id"] = "22222222-2222-4222-8222-222222222222/api/http"
    resolve_observation_documents([document(data)], as_of=AS_OF)
    data["service_instances"].append(dict(data["service_instances"][1]))  # type: ignore[union-attr,index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert "duplicate-service-id" in {item.code for item in caught.value.report.diagnostics}


def test_plan_contains_profiles_and_empty_task4_collections() -> None:
    plan = resolve_observation_documents([document(base_data())], as_of=AS_OF)
    assert [profile.id for profile in plan.service_profiles] == ["web"]
    assert plan.operations_views == ()
    assert plan.readiness_suites == ()


def test_duplicate_backend_identity_is_rejected() -> None:
    data = base_data()
    backend = {"id": "metrics", "kind": "metrics", "backend_ref": "opaque"}
    data["observation_backends"] = [backend, dict(backend)]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert "duplicate-observation-backend-id" in {
        item.code for item in caught.value.report.diagnostics
    }


def test_cross_reference_diagnostic_points_to_exact_field() -> None:
    data = base_data()
    data["dependency_contracts"][0]["target_endpoint_id"] = "missing/x"  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item for item in caught.value.report.diagnostics if item.code == "unknown-endpoint"
    )
    assert finding.location.pointer == "/dependency_contracts/0/target_endpoint_id"


@pytest.mark.parametrize("legacy_field", ["health_signal_ids", "health_signal_refs"])
def test_dependency_rejects_legacy_health_fields(legacy_field: str) -> None:
    data = base_data()
    edge = data["dependency_contracts"][0]  # type: ignore[index]
    edge[legacy_field] = ["reachable"]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item for item in caught.value.report.diagnostics if item.code == "invalid-document-record"
    )
    assert finding.location.pointer == f"/dependency_contracts/0/{legacy_field}"


def test_unknown_selected_endpoint_invalidates_plan_at_selection_pointer() -> None:
    data = base_data()
    data["service_instances"][0]["endpoint_ids"] = ["missing"]  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item for item in caught.value.report.diagnostics if item.code == "unknown-selected-endpoint"
    )
    assert finding.location.pointer == "/service_instances/0/endpoint_ids/0"


def test_duplicate_endpoint_overrides_are_ambiguous() -> None:
    data = base_data()
    override = {"endpoint_id": "http", "address": "frontend.internal"}
    data["service_instances"][0]["endpoint_overrides"] = [override, dict(override)]  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item
        for item in caught.value.report.diagnostics
        if item.code == "duplicate-endpoint-override"
    )
    assert finding.location.pointer == "/service_instances/0/endpoint_overrides/1/endpoint_id"


def test_override_requires_endpoint_to_be_explicitly_selected() -> None:
    data = base_data()
    instance = data["service_instances"][0]  # type: ignore[index]
    instance["endpoint_ids"] = ["http"]
    instance["endpoint_overrides"] = [{"endpoint_id": "other", "address": "internal"}]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert {item.code for item in caught.value.report.diagnostics} >= {"unknown-endpoint-override"}


def test_known_override_of_unselected_endpoint_is_rejected() -> None:
    data = base_data()
    profile = data["service_profiles"][0]  # type: ignore[index]
    profile["endpoints"].append({"id": "admin", "protocol": "http", "port": 8081})
    instance = data["service_instances"][0]  # type: ignore[index]
    instance["endpoint_ids"] = ["http"]
    instance["endpoint_overrides"] = [{"endpoint_id": "admin", "address": "internal"}]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert "endpoint-override-not-selected" in {
        item.code for item in caught.value.report.diagnostics
    }


@pytest.mark.parametrize(
    ("section", "index", "field", "value"),
    [
        ("service_profiles", 0, "endpoints", [{"id": "http", "protocol": "http", "port": "8080"}]),
        ("dependency_contracts", 0, "required", "true"),
        ("dependency_contracts", 0, "port", 8080.0),
    ],
)
def test_source_records_reject_scalar_coercion(
    section: str, index: int, field: str, value: object
) -> None:
    data = base_data()
    data[section][index][field] = value  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert "invalid-document-record" in {item.code for item in caught.value.report.diagnostics}


@pytest.mark.parametrize(
    ("expires_on", "expired"),
    [("2026-08-03", True), ("2026-08-04", True), ("2026-08-05", False)],
)
def test_waiver_expiry_is_fail_closed_on_as_of_date(expires_on: str, expired: bool) -> None:
    data = base_data()
    data["waivers"] = [
        {
            "id": "temporary",
            "scope": {
                "kind": "signal",
                "ref": "service/11111111-1111-4111-8111-111111111111/api/ready/up",
            },
            "owner": "ops",
            "reason": "migration",
            "created_on": "2026-01-01",
            "expires_on": expires_on,
        }
    ]
    if expired:
        with pytest.raises(PlanValidationError) as caught:
            resolve_observation_documents([document(data)], as_of=AS_OF)
        assert "waiver-expired" in {item.code for item in caught.value.report.diagnostics}
    else:
        plan = resolve_observation_documents([document(data)], as_of=AS_OF)
        assert [waiver.id for waiver in plan.waivers] == ["temporary"]


def test_noncanonical_uuid_spelling_cannot_alias_existing_host_identity() -> None:
    data = base_data()
    data["hosts"] = [
        {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"},
        {"id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
    ]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item for item in caught.value.report.diagnostics if item.code == "invalid-document-record"
    )
    assert finding.location.pointer == "/hosts/1/id"


def test_non_json_record_returns_diagnostic_instead_of_serialization_error() -> None:
    data = base_data()
    data["provider_aliases"][0]["metadata"] = {"labels": {"one", "two"}}  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item for item in caught.value.report.diagnostics if item.code == "invalid-document-record"
    )
    assert finding.location.pointer == "/provider_aliases/0"


def test_circular_record_returns_diagnostic_instead_of_recursion_error() -> None:
    data = base_data()
    metadata: dict[str, object] = {}
    metadata["cycle"] = metadata
    data["provider_aliases"][0]["metadata"] = metadata  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item for item in caught.value.report.diagnostics if item.code == "invalid-document-record"
    )
    assert finding.location.pointer == "/provider_aliases/0"
