from __future__ import annotations

import pytest
from pydantic import ValidationError

from infralink.observation.models_v2 import EdgeScope
from infralink.observation.v2 import (
    V2MetricValidationError,
    V2ResourceValidationError,
    V2TopologyValidationError,
    parse_v2_document,
    plan_v2_metric_contracts,
)

HOST_ID = "11111111-1111-4111-8111-111111111111"


def test_component_resource_slots_bind_typed_inputs_without_secret_values() -> None:
    parsed = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "external_service_contracts": [{"id": "mariadb-primary", "kind": "mariadb"}],
            "provider_aliases": [
                {"id": "bws", "provider": "bws", "project": "infra", "object_id": "archive-worker"}
            ],
            "secret_references": [{"id": "archive-worker", "provider_alias_id": "bws"}],
            "service_profiles": [
                {
                    "id": "archive-worker",
                    "components": [
                        {
                            "id": "worker",
                            "endpoints": [],
                            "resource_slots": [
                                {"id": "config", "kind": "config", "required": True},
                                {"id": "credentials", "kind": "secret", "required": True},
                                {"id": "data", "kind": "storage", "required": True},
                                {
                                    "id": "database",
                                    "kind": "external-service",
                                    "contract_ref": "mariadb-primary",
                                },
                            ],
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "archive",
                    "host_id": HOST_ID,
                    "profile_id": "archive-worker",
                    "components": [
                        {
                            "slot_id": "worker",
                            "resource_bindings": [
                                {"resource_id": "config", "reference": "archive-worker.yml"},
                                {"resource_id": "credentials", "reference": "archive-worker"},
                                {"resource_id": "data", "reference": "/data/archive"},
                                {"resource_id": "database", "reference": "mariadb-primary"},
                            ],
                        }
                    ],
                }
            ],
        }
    )

    component = parsed.service_profiles[0].components[0]
    assert [slot.kind.value for slot in component.resource_slots] == [
        "config",
        "secret",
        "storage",
        "external-service",
    ]


def test_component_resource_slots_reject_missing_required_binding() -> None:
    with pytest.raises(ValueError, match="required component resource slot is unbound"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "worker",
                        "components": [
                            {
                                "id": "worker",
                                "endpoints": [],
                                "resource_slots": [
                                    {"id": "data", "kind": "storage", "required": True}
                                ],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "archive",
                        "host_id": HOST_ID,
                        "profile_id": "worker",
                        "components": [{"slot_id": "worker"}],
                    }
                ],
            }
        )


def test_component_secret_resource_rejects_inline_value() -> None:
    with pytest.raises(V2ResourceValidationError, match="declared value-free secret reference"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "worker",
                        "components": [
                            {
                                "id": "worker",
                                "endpoints": [],
                                "resource_slots": [{"id": "credentials", "kind": "secret"}],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "archive",
                        "host_id": HOST_ID,
                        "profile_id": "worker",
                        "components": [
                            {
                                "slot_id": "worker",
                                "resource_bindings": [
                                    {
                                        "resource_id": "credentials",
                                        "reference": "actual-secret-value",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )


def test_external_service_resource_requires_declared_contract() -> None:
    with pytest.raises(V2ResourceValidationError, match="unknown contract"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "worker",
                        "components": [
                            {
                                "id": "worker",
                                "endpoints": [],
                                "resource_slots": [
                                    {
                                        "id": "database",
                                        "kind": "external-service",
                                        "contract_ref": "missing",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "archive",
                        "host_id": HOST_ID,
                        "profile_id": "worker",
                        "components": [
                            {
                                "slot_id": "worker",
                                "resource_bindings": [
                                    {"resource_id": "database", "reference": "missing"}
                                ],
                            }
                        ],
                    }
                ],
            }
        )


def test_component_metric_contract_projects_once_for_every_observer() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "web",
                    "components": [
                        {
                            "id": "nginx",
                            "endpoints": [
                                {
                                    "id": "metrics",
                                    "protocol": "http",
                                    "port": 9113,
                                }
                            ],
                            "metrics": [
                                {
                                    "id": "requests",
                                    "endpoint_id": "metrics",
                                    "path": "/metrics",
                                    "metric_name": "nginx_http_requests_total",
                                    "unit": "requests",
                                    "allowed_labels": ["environment"],
                                    "health_query": "sum(nginx_http_requests_total)",
                                    "condition": {"operator": "gte", "threshold": 0},
                                    "readiness_required": True,
                                }
                            ],
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "edge",
                    "host_id": HOST_ID,
                    "profile_id": "web",
                    "components": [
                        {
                            "slot_id": "nginx",
                            "endpoint_bindings": [
                                {"endpoint_id": "metrics", "address": "100.64.0.10"}
                            ],
                            "metric_bindings": [
                                {
                                    "metric_id": "requests",
                                    "labels": {"environment": "production"},
                                }
                            ],
                        }
                    ],
                },
                {
                    "id": "edge",
                    "host_id": "22222222-2222-4222-8222-222222222222",
                    "profile_id": "web",
                    "components": [
                        {
                            "slot_id": "nginx",
                            "endpoint_bindings": [
                                {"endpoint_id": "metrics", "address": "100.64.0.11"}
                            ],
                            "metric_bindings": [
                                {
                                    "metric_id": "requests",
                                    "labels": {"environment": "production"},
                                }
                            ],
                        }
                    ],
                },
            ],
        }
    )

    projections = plan_v2_metric_contracts((document,))

    assert len(projections) == 2
    projection = next(item for item in projections if item.id.startswith(HOST_ID))
    assert projection.id == f"{HOST_ID}/edge/nginx/requests"
    assert projection.prometheus.endpoint_id == f"{HOST_ID}/edge/nginx/metrics"
    assert (
        projection.prometheus.protocol,
        projection.prometheus.address,
        projection.prometheus.port,
    ) == (
        "http",
        "100.64.0.10",
        9113,
    )
    assert projection.prometheus.path == "/metrics"
    assert projection.prometheus.labels == {"environment": "production"}
    assert projection.gatus.endpoint_id == f"{HOST_ID}/edge/nginx/metrics"
    assert (projection.gatus.protocol, projection.gatus.address, projection.gatus.port) == (
        "http",
        "100.64.0.10",
        9113,
    )
    assert projection.gatus.path == "/metrics"
    assert projection.grafana.metric_name == "nginx_http_requests_total"
    assert projection.grafana.unit == "requests"
    assert projection.grafana.labels == {"environment": "production"}
    assert projection.doctor.required is True
    assert projection.doctor.query == "sum(nginx_http_requests_total)"
    assert projection.doctor.operator == "gte"
    assert projection.doctor.threshold == 0
    assert {item.prometheus.address for item in projections} == {"100.64.0.10", "100.64.0.11"}


def test_component_metric_contract_rejects_instance_label_outside_contract() -> None:
    with pytest.raises(ValueError, match="metric label is not allowed by component contract"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "web",
                        "components": [
                            {
                                "id": "nginx",
                                "endpoints": [{"id": "metrics", "protocol": "http", "port": 9113}],
                                "metrics": [
                                    {
                                        "id": "requests",
                                        "endpoint_id": "metrics",
                                        "path": "/metrics",
                                        "metric_name": "nginx_http_requests_total",
                                        "unit": "requests",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "edge",
                        "host_id": HOST_ID,
                        "profile_id": "web",
                        "components": [
                            {
                                "slot_id": "nginx",
                                "endpoint_bindings": [
                                    {"endpoint_id": "metrics", "address": "100.64.0.10"}
                                ],
                                "metric_bindings": [
                                    {"metric_id": "requests", "labels": {"secret": "value"}}
                                ],
                            }
                        ],
                    }
                ],
            }
        )


def test_readiness_required_metric_requires_evaluable_threshold() -> None:
    with pytest.raises(ValidationError, match="readiness-required metric"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "web",
                        "components": [
                            {
                                "id": "nginx",
                                "endpoints": [{"id": "metrics", "protocol": "http", "port": 9113}],
                                "metrics": [
                                    {
                                        "id": "requests",
                                        "endpoint_id": "metrics",
                                        "path": "/metrics",
                                        "metric_name": "nginx_http_requests_total",
                                        "unit": "requests",
                                        "readiness_required": True,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )


def test_component_metric_contract_requires_instance_source_address_binding() -> None:
    with pytest.raises(V2MetricValidationError, match="source endpoint has no instance address"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "web",
                        "components": [
                            {
                                "id": "nginx",
                                "endpoints": [{"id": "metrics", "protocol": "http", "port": 9113}],
                                "metrics": [
                                    {
                                        "id": "requests",
                                        "endpoint_id": "metrics",
                                        "path": "/metrics",
                                        "metric_name": "nginx_http_requests_total",
                                        "unit": "requests",
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "edge",
                        "host_id": HOST_ID,
                        "profile_id": "web",
                        "components": [{"slot_id": "nginx"}],
                    }
                ],
            }
        )


def test_parse_v2_document_keeps_component_endpoint_edges_typed() -> None:
    parsed = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "proxy",
                    "components": [
                        {
                            "id": "nginx",
                            "endpoints": [{"id": "http", "protocol": "http", "port": 8080}],
                        },
                        {
                            "id": "application",
                            "endpoints": [{"id": "http", "protocol": "http", "port": 8000}],
                        },
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "api",
                    "host_id": HOST_ID,
                    "profile_id": "proxy",
                    "components": [{"slot_id": "nginx"}, {"slot_id": "application"}],
                }
            ],
            "component_edges": [
                {
                    "id": "nginx-to-application",
                    "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                    "target_endpoint_id": f"{HOST_ID}/api/application/http",
                }
            ],
        }
    )

    assert parsed.component_edges[0].scope is EdgeScope.INTRA_SERVICE
    assert parsed.service_instances[0].components[0].slot_id == "nginx"


def test_parse_v2_document_rejects_duplicate_component_edge_ids() -> None:
    with pytest.raises(ValidationError, match="duplicate component edge id"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "component_edges": [
                    {
                        "id": "duplicate",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/application/http",
                    },
                    {
                        "id": "duplicate",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/application/http",
                    },
                ],
            }
        )


@pytest.mark.parametrize(
    ("collection", "items", "message"),
    [
        (
            "service_profiles",
            [
                {
                    "id": "proxy",
                    "components": [
                        {
                            "id": "nginx",
                            "endpoints": [{"id": "http", "protocol": "http", "port": 80}],
                        }
                    ],
                },
                {
                    "id": "proxy",
                    "components": [
                        {
                            "id": "nginx",
                            "endpoints": [{"id": "http", "protocol": "http", "port": 80}],
                        }
                    ],
                },
            ],
            "duplicate service profile id",
        ),
        (
            "service_instances",
            [
                {
                    "id": "api",
                    "host_id": HOST_ID,
                    "profile_id": "proxy",
                    "components": [{"slot_id": "nginx"}],
                },
                {
                    "id": "api",
                    "host_id": HOST_ID,
                    "profile_id": "proxy",
                    "components": [{"slot_id": "nginx"}],
                },
            ],
            "duplicate service instance id on host",
        ),
    ],
)
def test_parse_v2_document_rejects_duplicate_topology_identities(
    collection: str, items: list[object], message: str
) -> None:
    with pytest.raises(ValidationError, match=message):
        parse_v2_document({"schema_version": "infralink.observation/v2", collection: items})


def test_parse_v2_document_rejects_component_edge_to_unknown_endpoint() -> None:
    with pytest.raises(V2TopologyValidationError, match="unknown component endpoint"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "proxy",
                        "components": [
                            {
                                "id": "nginx",
                                "endpoints": [{"id": "http", "protocol": "http", "port": 8080}],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "api",
                        "host_id": HOST_ID,
                        "profile_id": "proxy",
                        "components": [{"slot_id": "nginx"}],
                    }
                ],
                "component_edges": [
                    {
                        "id": "bad-edge",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/nginx/missing",
                    }
                ],
            }
        )


def test_parse_v2_document_allows_endpointless_components() -> None:
    parsed = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "certificate-maintenance",
                    "components": [{"id": "acme-client", "endpoints": []}],
                }
            ],
            "service_instances": [
                {
                    "id": "edge-certificates",
                    "host_id": HOST_ID,
                    "profile_id": "certificate-maintenance",
                    "components": [{"slot_id": "acme-client"}],
                }
            ],
        }
    )

    assert parsed.service_profiles[0].components[0].endpoints == []


def test_parse_v2_document_rejects_duplicate_component_edge_semantics() -> None:
    with pytest.raises(V2TopologyValidationError, match="duplicate component edge semantics"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "proxy",
                        "components": [
                            {
                                "id": "nginx",
                                "endpoints": [{"id": "http", "protocol": "http", "port": 8080}],
                            },
                            {
                                "id": "application",
                                "endpoints": [{"id": "http", "protocol": "http", "port": 8000}],
                            },
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "api",
                        "host_id": HOST_ID,
                        "profile_id": "proxy",
                        "components": [{"slot_id": "nginx"}, {"slot_id": "application"}],
                    }
                ],
                "component_edges": [
                    {
                        "id": "nginx-to-application",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/application/http",
                    },
                    {
                        "id": "duplicate-meaning",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/application/http",
                    },
                ],
            }
        )


def test_parse_v2_document_rejects_incompatible_component_endpoint_protocols() -> None:
    with pytest.raises(
        V2TopologyValidationError, match="incompatible component endpoint protocols"
    ):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "proxy",
                        "components": [
                            {
                                "id": "nginx",
                                "endpoints": [{"id": "http", "protocol": "http", "port": 8080}],
                            },
                            {
                                "id": "application",
                                "endpoints": [{"id": "tls", "protocol": "https", "port": 8443}],
                            },
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "api",
                        "host_id": HOST_ID,
                        "profile_id": "proxy",
                        "components": [{"slot_id": "nginx"}, {"slot_id": "application"}],
                    }
                ],
                "component_edges": [
                    {
                        "id": "nginx-to-application",
                        "source_endpoint_id": f"{HOST_ID}/api/nginx/http",
                        "target_endpoint_id": f"{HOST_ID}/api/application/tls",
                    }
                ],
            }
        )
