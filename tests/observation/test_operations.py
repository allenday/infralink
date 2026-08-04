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
                            "requirement": "required",
                            "visualization": "status",
                        }
                    ],
                }
            ],
        }
    ]
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
