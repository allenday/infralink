from datetime import datetime, timezone
from types import MappingProxyType

import pytest

from infralink.observation.loader import ObservationDocument
from infralink.observation.planner import PlanValidationError, resolve_observation_documents

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
                "target_endpoint_id": "22222222-2222-4222-8222-222222222222/frontend/http",
                "protocol": "http",
                "port": 8080,
                "health_signal_ids": ["reachable"],
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
    edge = data["dependency_contracts"][0]  # type: ignore[index]
    edge.pop("health_signal_ids")
    edge["health_signal_ref"] = "dependency/api-to-frontend/health/reachable"
    plan = resolve_observation_documents([document(data)], as_of=AS_OF)
    assert plan.dependencies[0].health_signal_refs == (
        "dependency/api-to-frontend/health/reachable",
    )


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
