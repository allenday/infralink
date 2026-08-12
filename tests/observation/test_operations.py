from copy import deepcopy
from datetime import datetime, timezone
from types import MappingProxyType

import pytest
from tests.observation.test_planner import base_data

from infralink.observation.canonical import canonical_json
from infralink.observation.loader import ObservationDocument
from infralink.observation.planner import PlanValidationError, resolve_observation_documents

AS_OF = datetime(2026, 8, 4, tzinfo=timezone.utc)
SERVICE_SIGNAL = "service/11111111-1111-4111-8111-111111111111/api/ready/up"


def document(data: dict[str, object], digest: str = "semantic") -> ObservationDocument:
    return ObservationDocument("contract.yml", MappingProxyType(data), "raw", digest, 0)


def operational_data() -> dict[str, object]:
    data = base_data()
    data["observation_backends"] = [
        {"id": "health-primary", "kind": "health", "backend_ref": "health-prod"}
    ]
    data["datasource_bindings"] = [
        {
            "id": "primary-metrics",
            "observation_backend_id": "health-primary",
            "datasource_ref": "main",
        }
    ]
    data["operations_views"] = [
        {
            "id": "overview",
            "purpose": "production status",
            "sections": [
                {
                    "id": "core",
                    "title": "Core",
                    "members": [
                        {
                            "signal_id": "api-up",
                            "signal_ref": SERVICE_SIGNAL,
                            "datasource_binding_id": "primary-metrics",
                            "requirement": "required",
                            "visualization": "status",
                        }
                    ],
                }
            ],
        }
    ]
    data["readiness_suites"] = []
    data["readiness_suites"] = [
        {
            "id": "release",
            "members": [
                {
                    "id": "api-gate",
                    "signal_ref": "view/overview/query/core/api-up",
                    "cadence_seconds": 30,
                    "continuity_seconds": 60,
                    "freshness_seconds": 90,
                    "no_data_policy": "fail",
                    "error_policy": "fail",
                }
            ],
        }
    ]
    return data


def test_resolves_views_and_suites_with_stable_derived_signal_identity() -> None:
    plan = resolve_observation_documents([document(operational_data())], as_of=AS_OF)
    query = plan.operations_views[0].sections[0].members[0]
    assert query.id == "view/overview/query/core/api-up"
    assert query.signal_ref == SERVICE_SIGNAL
    assert query.evaluator == "capability-state"
    assert plan.readiness_suites[0].members[0].signal_ref == query.id
    assert plan.plan_digest
    assert plan.readiness_suites[0].suite_digest
    assert plan.readiness_suites[0].scoped_plan_digest
    assert canonical_json(plan) == canonical_json(plan)


def test_resolves_typed_fleet_convergence_view_without_query_members() -> None:
    data = deepcopy(operational_data())
    data["hosts"].append({"id": "33333333-3333-4333-8333-333333333333"})  # type: ignore[union-attr]
    data["hosts"][0]["self_deploy_v2_reconcile_enabled"] = True  # type: ignore[index]
    data["hosts"][1]["self_deploy_v2_reconcile_enabled"] = True  # type: ignore[index]
    data["operations_views"] = [
        {
            "id": "self-deploy",
            "purpose": "Fleet convergence",
            "kind": "fleet_convergence",
            "freshness_seconds": 900,
            "datasource_binding_id": "primary-metrics",
            "sections": [],
        }
    ]
    data["readiness_suites"] = []
    plan = resolve_observation_documents([document(data)], as_of=AS_OF)

    view = plan.operations_views[0]
    assert view.kind == "fleet_convergence"
    assert view.host_ids == (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    )
    assert view.freshness_seconds == 900
    assert view.datasource_binding_id == "primary-metrics"
    assert view.sections == ()


def test_fleet_convergence_rejects_declarative_host_membership() -> None:
    data = operational_data()
    data["operations_views"][0]["host_ids"] = ["11111111-1111-4111-8111-111111111111"]  # type: ignore[index]

    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)

    assert "invalid-document-record" in {item.code for item in caught.value.report.diagnostics}


def test_resolves_host_metrics_membership_from_declared_profile_instances() -> None:
    data = deepcopy(operational_data())
    data["service_profiles"][0]["required_host_baseline_capabilities"] = ["host-metrics"]  # type: ignore[index]
    for host in data["hosts"]:  # type: ignore[union-attr]
        host["baseline_capabilities"] = ["host-metrics"]
    data["operations_views"] = [
        {
            "id": "host-metrics",
            "purpose": "Default host resource metrics.",
            "kind": "host_metrics",
            "metric_profile_id": "web",
            "datasource_binding_id": "primary-metrics",
            "sections": [],
        }
    ]
    data["readiness_suites"] = []
    plan = resolve_observation_documents([document(data)], as_of=AS_OF)

    view = plan.operations_views[0]
    assert view.kind == "host_metrics"
    assert view.host_ids == (
        "11111111-1111-4111-8111-111111111111",
        "22222222-2222-4222-8222-222222222222",
    )
    assert view.metric_profile_id == "web"
    assert view.datasource_binding_id == "primary-metrics"


def test_host_metrics_rejects_a_profile_without_host_metrics_capability() -> None:
    data = deepcopy(operational_data())
    data["operations_views"] = [
        {
            "id": "host-metrics",
            "purpose": "Default host resource metrics.",
            "kind": "host_metrics",
            "metric_profile_id": "web",
            "datasource_binding_id": "primary-metrics",
            "sections": [],
        }
    ]
    data["readiness_suites"] = []

    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)

    assert "host-metrics-profile-capability-required" in {
        item.code for item in caught.value.report.diagnostics
    }


def test_plan_digest_is_exactly_public_canonical_plan_without_digest() -> None:
    from infralink.observation.canonical import canonical_digest

    plan = resolve_observation_documents([document(operational_data())], as_of=AS_OF)
    without_digest = plan.model_copy(update={"plan_digest": None})
    assert plan.plan_digest == canonical_digest(without_digest)
    assert b'"plan_digest"' not in canonical_json(without_digest)
    assert b'"label":null' not in canonical_json(without_digest)


def test_legacy_baseline_absence_preserves_global_and_scoped_digests() -> None:
    plan = resolve_observation_documents([document(operational_data())], as_of=AS_OF)

    assert plan.plan_digest == "a1af237c34002f93c0581ac80ddf34e5a4d64c331eb9110a9453adc1e0d85462"
    assert plan.readiness_suites[0].scoped_plan_digest == (
        "08a60dd20a5973cee67bb4de01b99fb88cf6497e2650ca4578a150c033baf04e"
    )


def test_legacy_view_signals_and_suite_policy_are_rejected() -> None:
    for section, key, value in [
        ("operations_views", "signals", []),
        ("readiness_suites", "policy", "must-pass"),
    ]:
        data = operational_data()
        if section == "operations_views":
            data[section][0][key] = value  # type: ignore[index]
        else:
            data[section][0]["members"][0][key] = value  # type: ignore[index]
        with pytest.raises(PlanValidationError):
            resolve_observation_documents([document(data)], as_of=AS_OF)


def test_view_requires_explicit_existing_datasource_binding() -> None:
    data = operational_data()
    data["operations_views"][0]["sections"][0]["members"][0][  # type: ignore[index]
        "datasource_binding_id"
    ] = "missing"
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert "unknown-view-datasource-binding" in {
        item.code for item in caught.value.report.diagnostics
    }


def test_view_on_view_is_rejected_independent_of_view_order() -> None:
    for reverse in (False, True):
        data = operational_data()
        second = deepcopy(data["operations_views"][0])  # type: ignore[index]
        second["id"] = "derived"
        second["sections"][0]["members"][0]["signal_id"] = "nested"  # type: ignore[index]
        second["sections"][0]["members"][0][  # type: ignore[index]
            "signal_ref"
        ] = "view/overview/query/core/api-up"
        data["operations_views"].append(second)  # type: ignore[union-attr]
        if reverse:
            data["operations_views"].reverse()  # type: ignore[union-attr]
        with pytest.raises(PlanValidationError) as caught:
            resolve_observation_documents([document(data)], as_of=AS_OF)
        finding = next(
            item for item in caught.value.report.diagnostics if item.code == "view-signal-ref-kind"
        )
        assert finding.location.pointer.endswith("/sections/0/members/0/signal_ref")


def test_view_datasource_backend_must_match_source_evaluator_kind() -> None:
    data = operational_data()
    data["observation_backends"][0]["kind"] = "logs"  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(
        item
        for item in caught.value.report.diagnostics
        if item.code == "view-datasource-kind-incompatible"
    )
    assert finding.location.pointer.endswith("/datasource_binding_id")


def test_dependency_view_query_uses_explicit_health_evaluator_and_backend() -> None:
    data = operational_data()
    member = data["operations_views"][0]["sections"][0]["members"][0]  # type: ignore[index]
    member["signal_ref"] = "dependency/api-to-frontend/health/reachable"
    plan = resolve_observation_documents([document(data)], as_of=AS_OF)
    query = plan.operations_views[0].sections[0].members[0]
    source = next(
        signal
        for signal in plan.signals
        if signal.id == "dependency/api-to-frontend/health/reachable"
    )
    assert source.evaluator == "dependency-health"
    assert query.evaluator == "dependency-health"


def test_dependency_view_query_rejects_non_health_backend() -> None:
    data = operational_data()
    member = data["operations_views"][0]["sections"][0]["members"][0]  # type: ignore[index]
    member["signal_ref"] = "dependency/api-to-frontend/health/reachable"
    data["observation_backends"][0]["kind"] = "metrics"  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert "view-datasource-kind-incompatible" in {
        item.code for item in caught.value.report.diagnostics
    }


@pytest.mark.parametrize(
    "field",
    [
        "id",
        "signal_ref",
        "cadence_seconds",
        "continuity_seconds",
        "freshness_seconds",
        "no_data_policy",
        "error_policy",
    ],
)
def test_suite_member_behavior_is_never_synthesized(field: str) -> None:
    data = operational_data()
    del data["readiness_suites"][0]["members"][0][field]  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert any(
        finding.code == "invalid-document-record"
        and finding.location.pointer.endswith(f"/members/0/{field}")
        for finding in caught.value.report.diagnostics
    )


@pytest.mark.parametrize(
    ("kind", "code"),
    [
        ("view", "duplicate-view-id"),
        ("section", "duplicate-view-section-id"),
        ("query", "duplicate-view-query-id"),
        ("suite", "duplicate-suite-id"),
        ("member", "duplicate-suite-member-id"),
    ],
)
def test_duplicate_operations_identities_are_typed(kind: str, code: str) -> None:
    data = operational_data()
    if kind == "view":
        data["operations_views"].append(deepcopy(data["operations_views"][0]))  # type: ignore[index,union-attr]
    elif kind == "section":
        sections = data["operations_views"][0]["sections"]  # type: ignore[index]
        sections.append(deepcopy(sections[0]))
    elif kind == "query":
        members = data["operations_views"][0]["sections"][0]["members"]  # type: ignore[index]
        members.append(deepcopy(members[0]))
    elif kind == "suite":
        data["readiness_suites"].append(deepcopy(data["readiness_suites"][0]))  # type: ignore[index,union-attr]
    else:
        members = data["readiness_suites"][0]["members"]  # type: ignore[index]
        members.append(deepcopy(members[0]))
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert code in {item.code for item in caught.value.report.diagnostics}


def test_missing_view_signal_is_reported_at_exact_pointer() -> None:
    data = operational_data()
    data["operations_views"][0]["sections"][0]["members"][0]["signal_ref"] = "missing"  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    finding = next(d for d in caught.value.report.diagnostics if d.code == "unknown-view-signal")
    assert finding.location.pointer.endswith("/sections/0/members/0/signal_ref")


def test_required_suite_gate_cannot_reference_optional_view_member() -> None:
    data = operational_data()
    data["operations_views"][0]["sections"][0]["members"][0]["requirement"] = "optional"  # type: ignore[index]
    with pytest.raises(PlanValidationError) as caught:
        resolve_observation_documents([document(data)], as_of=AS_OF)
    assert "optional-view-signal-gate" in {d.code for d in caught.value.report.diagnostics}


def test_global_and_scoped_digest_have_different_invalidation_boundaries() -> None:
    original = operational_data()
    changed = deepcopy(original)
    changed["service_profiles"].append(  # type: ignore[union-attr]
        {"id": "unused", "signals": [], "secret_slots": []}
    )
    first = resolve_observation_documents([document(original, "one")], as_of=AS_OF)
    second = resolve_observation_documents([document(changed, "two")], as_of=AS_OF)
    assert first.plan_digest != second.plan_digest
    assert (
        first.readiness_suites[0].scoped_plan_digest
        == second.readiness_suites[0].scoped_plan_digest
    )


def test_suite_definition_change_invalidates_suite_digest() -> None:
    data = operational_data()
    changed = deepcopy(data)
    changed["readiness_suites"][0]["members"][0]["freshness_seconds"] = 120  # type: ignore[index]
    first = resolve_observation_documents([document(data)], as_of=AS_OF)
    second = resolve_observation_documents([document(changed)], as_of=AS_OF)
    assert first.readiness_suites[0].suite_digest != second.readiness_suites[0].suite_digest


def test_unrelated_datasource_and_backend_do_not_change_scoped_digest() -> None:
    data = operational_data()
    changed = deepcopy(data)
    changed["observation_backends"].append(  # type: ignore[union-attr]
        {"id": "unused", "kind": "logs", "backend_ref": "logs-unused"}
    )
    changed["datasource_bindings"].append(  # type: ignore[union-attr]
        {
            "id": "unused-logs",
            "observation_backend_id": "unused",
            "datasource_ref": "unused",
        }
    )
    first = resolve_observation_documents([document(data)], as_of=AS_OF)
    second = resolve_observation_documents([document(changed)], as_of=AS_OF)
    assert (
        first.readiness_suites[0].scoped_plan_digest
        == second.readiness_suites[0].scoped_plan_digest
    )


def test_scoped_opaque_identity_matches_are_qualified_by_collection_kind() -> None:
    data = operational_data()
    data["renderer_binding_identities"] = [
        {
            "id": "primary-metrics",
            "renderer": "dashboard",
            "binding_ref": "unrelated/original",
        },
        {
            "id": "health-primary",
            "renderer": "dashboard",
            "binding_ref": "also-unrelated/original",
        },
    ]
    changed = deepcopy(data)
    changed["renderer_binding_identities"][0]["binding_ref"] = "unrelated/changed"  # type: ignore[index]
    changed["renderer_binding_identities"][1]["binding_ref"] = "also-unrelated/changed"  # type: ignore[index]
    assert _scoped(data) == _scoped(changed)


def _scoped(data: dict[str, object]) -> str:
    return (
        resolve_observation_documents([document(data)], as_of=AS_OF)
        .readiness_suites[0]
        .scoped_plan_digest
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["service_profiles"][0]["endpoints"][0].update(address="api.local"),
        lambda d: d["service_profiles"][0]["health"][0].update(expected_statuses=[200, 204]),
        lambda d: d["provider_aliases"][0].update(object_id="changed"),
        lambda d: d["renderer_binding_identities"][0].update(binding_ref="ops/changed"),
        lambda d: d["datasource_bindings"][0].update(datasource_ref="changed"),
        lambda d: d["observation_backends"][0].update(backend_ref="changed"),
        lambda d: d["operations_views"][0].update(purpose="changed purpose"),
        lambda d: d.setdefault("waivers", []).append(
            {
                "id": "active",
                "scope": {"kind": "signal", "ref": SERVICE_SIGNAL},
                "owner": "ops",
                "reason": "maintenance",
                "created_on": "2026-08-01",
                "expires_on": "2026-08-10",
            }
        ),
    ],
)
def test_relevant_transitive_fact_changes_scoped_digest(mutate: object) -> None:
    data = operational_data()
    data["renderer_binding_identities"] = [
        {
            "id": "dashboard",
            "renderer": "grafana",
            "binding_ref": "ops/default",
            "delivery_forms": ["file"],
        }
    ]
    data["secret_bindings"][0]["renderer_binding_id"] = "dashboard"  # type: ignore[index]
    changed = deepcopy(data)
    mutate(changed)  # type: ignore[operator]
    assert _scoped(data) != _scoped(changed)


def test_dependency_signal_scope_includes_both_services_edge_and_target_endpoint() -> None:
    data = base_data()
    data["readiness_suites"] = [
        {
            "id": "dependency-release",
            "members": [
                {
                    "id": "reachable",
                    "signal_ref": "dependency/api-to-frontend/health/reachable",
                    "cadence_seconds": 30,
                    "continuity_seconds": 60,
                    "freshness_seconds": 90,
                    "no_data_policy": "fail",
                    "error_policy": "fail",
                }
            ],
        }
    ]
    baseline = _scoped(data)
    changed = deepcopy(data)
    changed["service_profiles"][0]["endpoints"][0]["address"] = "changed.local"  # type: ignore[index]
    assert baseline != _scoped(changed)
    changed = deepcopy(data)
    changed["dependency_contracts"][0]["execution_adapter"] = "changed"  # type: ignore[index]
    assert baseline != _scoped(changed)


def test_unordered_source_collections_are_normalized_but_display_order_is_preserved() -> None:
    data = operational_data()
    data["operations_views"][0]["sections"].append(  # type: ignore[index,union-attr]
        {
            "id": "secondary",
            "members": [
                {
                    "signal_id": "api-up-secondary",
                    "signal_ref": SERVICE_SIGNAL,
                    "datasource_binding_id": "primary-metrics",
                    "requirement": "optional",
                }
            ],
        }
    )
    reordered = deepcopy(data)
    reordered["hosts"].reverse()  # type: ignore[union-attr]
    reordered["service_instances"].reverse()  # type: ignore[union-attr]
    first = resolve_observation_documents([document(data)], as_of=AS_OF)
    second = resolve_observation_documents([document(reordered)], as_of=AS_OF)
    assert canonical_json(first) == canonical_json(second)
    display_reordered = deepcopy(data)
    display_reordered["operations_views"][0]["sections"].reverse()  # type: ignore[index,union-attr]
    third = resolve_observation_documents([document(display_reordered)], as_of=AS_OF)
    assert canonical_json(first) != canonical_json(third)
    assert (
        first.readiness_suites[0].scoped_plan_digest != third.readiness_suites[0].scoped_plan_digest
    )
