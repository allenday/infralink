from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError
from pydantic import ValidationError

from infralink.observation.models import EndpointExposure
from infralink.observation.models_v2 import EdgeScope
from infralink.observation.v2 import (
    V2ArtifactValidationError,
    V2ConfigurationValidationError,
    V2InstanceTopologyValidationError,
    V2MetricValidationError,
    V2ResourceValidationError,
    V2TopologyValidationError,
    parse_v2_document,
    plan_v2_artifact_bindings,
    plan_v2_configuration_bindings,
    plan_v2_metric_contracts,
    validate_v2_documents,
)

HOST_ID = "11111111-1111-4111-8111-111111111111"


def test_component_endpoint_override_resolves_address_port_and_exposure() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "smtp-tenant",
                    "components": [
                        {
                            "id": "postfix",
                            "endpoints": [{"id": "submission", "protocol": "smtp", "port": 587}],
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "tenant-one",
                    "host_id": HOST_ID,
                    "profile_id": "smtp-tenant",
                    "components": [
                        {
                            "slot_id": "postfix",
                            "endpoint_overrides": [
                                {
                                    "endpoint_id": "submission",
                                    "address": "203.0.113.10",
                                    "port": 2587,
                                    "exposure": "public",
                                }
                            ],
                        }
                    ],
                }
            ],
            "component_edges": [
                {
                    "id": "smtp-to-smtp",
                    "source_endpoint_id": f"{HOST_ID}/tenant-one/postfix/submission",
                    "target_endpoint_id": f"{HOST_ID}/tenant-one/postfix/submission",
                }
            ],
        }
    )

    endpoints = validate_v2_documents((document,))

    endpoint = endpoints[f"{HOST_ID}/tenant-one/postfix/submission"]
    assert (endpoint.address, endpoint.port, endpoint.exposure) == (
        "203.0.113.10",
        2587,
        EndpointExposure.PUBLIC,
    )


def test_connection_configuration_slot_projects_its_declared_edge_target() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "application",
                    "components": [
                        {
                            "id": "worker",
                            "endpoints": [
                                {"id": "database", "protocol": "postgresql", "port": 5432}
                            ],
                        },
                        {
                            "id": "postgresql",
                            "endpoints": [
                                {"id": "postgresql", "protocol": "postgresql", "port": 5432}
                            ],
                        },
                    ],
                    "configuration_slots": [
                        {
                            "id": "metadata-database",
                            "component_id": "worker",
                            "kind": "connection",
                            "protocol": "postgresql",
                            "cardinality": "one",
                            "target_profile_id": "application",
                            "purpose": "Connect the worker to its metadata database.",
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "application",
                    "host_id": HOST_ID,
                    "profile_id": "application",
                    "components": [
                        {
                            "slot_id": "worker",
                            "endpoint_overrides": [
                                {"endpoint_id": "database", "address": "127.0.0.1"}
                            ],
                        },
                        {
                            "slot_id": "postgresql",
                            "endpoint_overrides": [
                                {"endpoint_id": "postgresql", "address": "100.64.0.10"}
                            ],
                        },
                    ],
                    "configuration_bindings": [
                        {"slot_id": "metadata-database", "edge_refs": ["worker-to-postgresql"]}
                    ],
                }
            ],
            "component_edges": [
                {
                    "id": "worker-to-postgresql",
                    "source_endpoint_id": f"{HOST_ID}/application/worker/database",
                    "target_endpoint_id": f"{HOST_ID}/application/postgresql/postgresql",
                }
            ],
        }
    )

    binding = plan_v2_configuration_bindings((document,))[0]

    assert binding.edge_refs == ["worker-to-postgresql"]
    assert [
        (endpoint.address, endpoint.port, endpoint.protocol) for endpoint in binding.targets
    ] == [("100.64.0.10", 5432, "postgresql")]


def test_connection_configuration_slot_rejects_another_instance_source_edge() -> None:
    payload = {
        "schema_version": "infralink.observation/v2",
        "service_profiles": [
            {
                "id": "application",
                "components": [
                    {
                        "id": "worker",
                        "endpoints": [{"id": "database", "protocol": "postgresql", "port": 5432}],
                    },
                    {
                        "id": "postgresql",
                        "endpoints": [{"id": "postgresql", "protocol": "postgresql", "port": 5432}],
                    },
                ],
                "configuration_slots": [
                    {
                        "id": "metadata-database",
                        "component_id": "worker",
                        "kind": "connection",
                        "protocol": "postgresql",
                        "cardinality": "one",
                        "target_profile_id": "application",
                        "purpose": "Connect the worker to its metadata database.",
                    }
                ],
            }
        ],
        "service_instances": [
            {
                "id": "application-one",
                "host_id": HOST_ID,
                "profile_id": "application",
                "components": [
                    {
                        "slot_id": "worker",
                        "endpoint_overrides": [{"endpoint_id": "database", "address": "127.0.0.1"}],
                    },
                    {
                        "slot_id": "postgresql",
                        "endpoint_overrides": [
                            {"endpoint_id": "postgresql", "address": "100.64.0.10"}
                        ],
                    },
                ],
                "configuration_bindings": [
                    {"slot_id": "metadata-database", "edge_refs": ["two-worker-to-postgresql"]}
                ],
            },
            {
                "id": "application-two",
                "host_id": HOST_ID,
                "profile_id": "application",
                "components": [
                    {
                        "slot_id": "worker",
                        "endpoint_overrides": [{"endpoint_id": "database", "address": "127.0.0.1"}],
                    },
                    {
                        "slot_id": "postgresql",
                        "endpoint_overrides": [
                            {"endpoint_id": "postgresql", "address": "100.64.0.11"}
                        ],
                    },
                ],
                "configuration_bindings": [
                    {"slot_id": "metadata-database", "edge_refs": ["two-worker-to-postgresql"]}
                ],
            },
        ],
        "component_edges": [
            {
                "id": "two-worker-to-postgresql",
                "source_endpoint_id": f"{HOST_ID}/application-two/worker/database",
                "target_endpoint_id": f"{HOST_ID}/application-two/postgresql/postgresql",
            }
        ],
    }

    with pytest.raises(V2ConfigurationValidationError) as caught:
        parse_v2_document(payload)

    assert caught.value.code == "service-instance-connection-source-component-mismatch"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (
            lambda payload: payload["service_instances"][0]["configuration_bindings"][0].update(
                {"value": "manual", "edge_refs": None}
            ),
            "service-instance-connection-binding-invalid-source",
        ),
        (
            lambda payload: payload["service_instances"][0]["configuration_bindings"][0].update(
                {"edge_refs": []}
            ),
            "service-instance-connection-cardinality-invalid",
        ),
        (
            lambda payload: payload["component_edges"][0].update(
                {"source_endpoint_id": f"{HOST_ID}/application/postgresql/postgresql"}
            ),
            "service-instance-connection-source-component-mismatch",
        ),
        (
            lambda payload: payload["service_profiles"][0]["configuration_slots"][0].update(
                {"protocol": "tcp"}
            ),
            "service-instance-connection-protocol-mismatch",
        ),
        (
            lambda payload: payload["service_profiles"][0]["configuration_slots"][0].update(
                {"target_profile_id": "different-profile"}
            ),
            "service-instance-connection-target-profile-mismatch",
        ),
    ],
)
def test_connection_configuration_slot_rejects_invalid_edge_contracts(
    mutate: object, code: str
) -> None:
    payload = {
        "schema_version": "infralink.observation/v2",
        "service_profiles": [
            {
                "id": "application",
                "components": [
                    {
                        "id": "worker",
                        "endpoints": [{"id": "database", "protocol": "postgresql", "port": 5432}],
                    },
                    {
                        "id": "postgresql",
                        "endpoints": [{"id": "postgresql", "protocol": "postgresql", "port": 5432}],
                    },
                ],
                "configuration_slots": [
                    {
                        "id": "metadata-database",
                        "component_id": "worker",
                        "kind": "connection",
                        "protocol": "postgresql",
                        "cardinality": "one",
                        "target_profile_id": "application",
                        "purpose": "Connect the worker to its metadata database.",
                    }
                ],
            }
        ],
        "service_instances": [
            {
                "id": "application",
                "host_id": HOST_ID,
                "profile_id": "application",
                "components": [
                    {
                        "slot_id": "worker",
                        "endpoint_overrides": [{"endpoint_id": "database", "address": "127.0.0.1"}],
                    },
                    {
                        "slot_id": "postgresql",
                        "endpoint_overrides": [
                            {"endpoint_id": "postgresql", "address": "100.64.0.10"}
                        ],
                    },
                ],
                "configuration_bindings": [
                    {"slot_id": "metadata-database", "edge_refs": ["worker-to-postgresql"]}
                ],
            }
        ],
        "component_edges": [
            {
                "id": "worker-to-postgresql",
                "source_endpoint_id": f"{HOST_ID}/application/worker/database",
                "target_endpoint_id": f"{HOST_ID}/application/postgresql/postgresql",
            }
        ],
    }

    assert callable(mutate)
    mutate(payload)

    with pytest.raises(V2ConfigurationValidationError) as caught:
        parse_v2_document(payload)

    assert caught.value.code == code


def test_many_connection_configuration_slot_accepts_an_explicit_empty_edge_set() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "elasticsearch-node",
                    "components": [{"id": "elasticsearch", "endpoints": []}],
                    "configuration_slots": [
                        {
                            "id": "transport-peers",
                            "component_id": "elasticsearch",
                            "kind": "connection",
                            "protocol": "tcp",
                            "cardinality": "many",
                            "purpose": "Join zero or more Elasticsearch transport peers.",
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "elasticsearch",
                    "host_id": HOST_ID,
                    "profile_id": "elasticsearch-node",
                    "components": [{"slot_id": "elasticsearch"}],
                    "configuration_bindings": [{"slot_id": "transport-peers", "edge_refs": []}],
                }
            ],
        }
    )

    binding = plan_v2_configuration_bindings((document,))[0]

    assert binding.edge_refs == []
    assert binding.targets == []


def test_component_endpoint_binding_accepts_multiple_addresses_with_first_as_canonical() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "smtp-tenant",
                    "components": [
                        {
                            "id": "postfix",
                            "endpoints": [{"id": "submission", "protocol": "smtp", "port": 587}],
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "tenant-one",
                    "host_id": HOST_ID,
                    "profile_id": "smtp-tenant",
                    "components": [
                        {
                            "slot_id": "postfix",
                            "endpoint_bindings": [
                                {
                                    "endpoint_id": "submission",
                                    "addresses": ["203.0.113.10", "203.0.113.11"],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    endpoint = validate_v2_documents((document,))[f"{HOST_ID}/tenant-one/postfix/submission"]

    assert endpoint.address == "203.0.113.10"


def test_component_metric_accepts_endpoint_override_as_its_address_binding() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "exporter",
                    "components": [
                        {
                            "id": "exporter",
                            "endpoints": [{"id": "metrics", "protocol": "http", "port": 9100}],
                            "metrics": [
                                {
                                    "id": "healthy",
                                    "endpoint_id": "metrics",
                                    "path": "/metrics",
                                    "metric_name": "process_start_time_seconds",
                                    "unit": "seconds",
                                }
                            ],
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "exporter",
                    "host_id": HOST_ID,
                    "profile_id": "exporter",
                    "components": [
                        {
                            "slot_id": "exporter",
                            "endpoint_overrides": [
                                {"endpoint_id": "metrics", "address": "100.64.0.1"}
                            ],
                        }
                    ],
                }
            ],
        }
    )

    assert plan_v2_metric_contracts((document,))[0].prometheus.address == "100.64.0.1"


@pytest.mark.parametrize(
    "binding",
    [
        {"endpoint_id": "submission", "addresses": []},
        {
            "endpoint_id": "submission",
            "address": "203.0.113.10",
            "addresses": ["203.0.113.11"],
        },
    ],
)
def test_endpoint_binding_schema_rejects_the_same_invalid_address_forms_as_model(
    binding: dict[str, object],
) -> None:
    schema_path = Path(__file__).parents[2] / "src/infralink/schemas/observation/v2/document.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    endpoint_binding_schema = schema["$defs"]["EndpointBinding"]

    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(endpoint_binding_schema).validate(binding)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"endpoint_id": "missing", "port": 2587}, "unknown component endpoint"),
        (
            {"endpoint_id": "submission", "protocol": "tcp"},
            "Extra inputs are not permitted",
        ),
    ],
)
def test_component_endpoint_override_rejects_unknown_endpoint_or_protocol_mutation(
    override: dict[str, object], message: str
) -> None:
    with pytest.raises((ValidationError, V2InstanceTopologyValidationError), match=message):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "smtp-tenant",
                        "components": [
                            {
                                "id": "postfix",
                                "endpoints": [
                                    {"id": "submission", "protocol": "smtp", "port": 587}
                                ],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "tenant-one",
                        "host_id": HOST_ID,
                        "profile_id": "smtp-tenant",
                        "components": [{"slot_id": "postfix", "endpoint_overrides": [override]}],
                    }
                ],
            }
        )


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


def test_profile_configuration_slots_resolve_typed_component_bindings() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "irc-stack",
                    "components": [{"id": "inspircd", "endpoints": []}],
                    "configuration_slots": [
                        {
                            "id": "tenant-stacks",
                            "component_id": "inspircd",
                            "kind": "record-list",
                            "identity_field": "id",
                            "purpose": "Declare IRC tenant stack identity and hostnames.",
                            "fields": [
                                {"id": "id", "kind": "string"},
                                {"id": "redis-database", "kind": "integer"},
                                {"id": "irc-hosts", "kind": "string-list"},
                            ],
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "staging",
                    "host_id": HOST_ID,
                    "profile_id": "irc-stack",
                    "components": [{"slot_id": "inspircd"}],
                    "configuration_bindings": [
                        {
                            "slot_id": "tenant-stacks",
                            "value": [
                                {
                                    "id": "platform",
                                    "redis-database": 1,
                                    "irc-hosts": ["irc.example.test"],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    binding = document.service_instances[0].configuration_bindings[0]
    assert binding.slot_id == "tenant-stacks"
    assert binding.value == [
        {
            "id": "platform",
            "redis-database": 1,
            "irc-hosts": ["irc.example.test"],
        }
    ]

    resolved = plan_v2_configuration_bindings((document,))
    assert [(item.component_id, item.slot_id, item.value) for item in resolved] == [
        (
            "inspircd",
            "tenant-stacks",
            [
                {
                    "id": "platform",
                    "redis-database": 1,
                    "irc-hosts": ["irc.example.test"],
                }
            ],
        )
    ]


def test_profile_wide_configuration_slot_has_no_component_owner() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "observability",
                    "components": [{"id": "grafana", "endpoints": []}],
                    "configuration_slots": [
                        {
                            "id": "datasource",
                            "kind": "record",
                            "purpose": "Bind the datasource materialization contract.",
                            "fields": [{"id": "artifact", "kind": "string"}],
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "observability",
                    "host_id": HOST_ID,
                    "profile_id": "observability",
                    "components": [{"slot_id": "grafana"}],
                    "configuration_bindings": [
                        {"slot_id": "datasource", "value": {"artifact": "datasource.yaml"}}
                    ],
                }
            ],
        }
    )

    assert plan_v2_configuration_bindings((document,))[0].component_id is None


def test_profile_artifact_slot_resolves_integrity_bound_file_delivery() -> None:
    document = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [
                {
                    "id": "observability",
                    "components": [{"id": "grafana", "endpoints": []}],
                    "artifact_slots": [
                        {
                            "id": "datasource-provisioning",
                            "component_id": "grafana",
                            "kind": "file",
                            "target": "grafana/provisioning/datasources.yml",
                            "mode": 416,
                            "owner_uid": 472,
                            "owner_gid": 472,
                            "consumer_id": "grafana",
                            "lifecycle": "compose-recreate",
                            "purpose": "Provision Grafana's declared datasource.",
                        }
                    ],
                }
            ],
            "service_instances": [
                {
                    "id": "observability",
                    "host_id": HOST_ID,
                    "profile_id": "observability",
                    "components": [{"slot_id": "grafana"}],
                    "artifact_bindings": [
                        {
                            "slot_id": "datasource-provisioning",
                            "sources": [
                                {
                                    "path": "operations/observation/rendered/grafana/datasources.yml",
                                    "sha256": "cfdd3d870458d66f175c68f09f6e0c8df1c717963348d995f58017762773b63b",
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    )

    resolved = plan_v2_artifact_bindings((document,))

    assert [
        (
            item.host_id,
            item.service_instance_id,
            item.component_id,
            item.slot.target,
            item.sources[0].path,
            item.sources[0].sha256,
        )
        for item in resolved
    ] == [
        (
            HOST_ID,
            "observability",
            "grafana",
            "grafana/provisioning/datasources.yml",
            "operations/observation/rendered/grafana/datasources.yml",
            "cfdd3d870458d66f175c68f09f6e0c8df1c717963348d995f58017762773b63b",
        )
    ]


def test_tree_artifact_requires_each_selected_source_to_have_a_relative_target() -> None:
    with pytest.raises(V2ArtifactValidationError, match="require relative_target"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "observability",
                        "components": [{"id": "grafana", "endpoints": []}],
                        "artifact_slots": [
                            {
                                "id": "dashboards",
                                "component_id": "grafana",
                                "kind": "tree",
                                "target": "grafana/dashboards",
                                "mode": 420,
                                "owner_uid": 472,
                                "owner_gid": 472,
                                "consumer_id": "grafana",
                                "lifecycle": "compose-recreate",
                                "purpose": "Provision selected Grafana dashboards.",
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "observability",
                        "host_id": HOST_ID,
                        "profile_id": "observability",
                        "components": [{"slot_id": "grafana"}],
                        "artifact_bindings": [
                            {
                                "slot_id": "dashboards",
                                "sources": [
                                    {
                                        "path": "catalog/grafana/host-metrics.json",
                                        "sha256": "a" * 64,
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        )


def test_profile_artifact_slots_reject_overlapping_logical_targets() -> None:
    with pytest.raises(ValueError, match="artifact slot targets must not overlap"):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "observability",
                        "components": [{"id": "grafana", "endpoints": []}],
                        "artifact_slots": [
                            {
                                "id": "provisioning",
                                "kind": "file",
                                "target": "grafana/provisioning",
                                "mode": 420,
                                "owner_uid": 472,
                                "owner_gid": 472,
                                "consumer_id": "grafana",
                                "lifecycle": "compose-recreate",
                                "purpose": "Install provisioning configuration.",
                            },
                            {
                                "id": "datasource",
                                "kind": "file",
                                "target": "grafana/provisioning/datasources.yml",
                                "mode": 420,
                                "owner_uid": 472,
                                "owner_gid": 472,
                                "consumer_id": "grafana",
                                "lifecycle": "compose-recreate",
                                "purpose": "Install datasource configuration.",
                            },
                        ],
                    }
                ],
            }
        )


def test_record_list_configuration_accepts_typed_hostname_list_maps_and_rejects_duplicates() -> (
    None
):
    source = {
        "schema_version": "infralink.observation/v2",
        "service_profiles": [
            {
                "id": "irc-stack",
                "components": [{"id": "inspircd", "endpoints": []}],
                "configuration_slots": [
                    {
                        "id": "tenant-stacks",
                        "kind": "record-list",
                        "identity_field": "id",
                        "purpose": "Declare IRC tenant stack identity and hostnames.",
                        "fields": [
                            {"id": "id", "kind": "string"},
                            {"id": "hosts", "kind": "string-list-map"},
                        ],
                    }
                ],
            }
        ],
        "service_instances": [
            {
                "id": "staging",
                "host_id": HOST_ID,
                "profile_id": "irc-stack",
                "components": [{"slot_id": "inspircd"}],
                "configuration_bindings": [
                    {
                        "slot_id": "tenant-stacks",
                        "value": [{"id": "platform", "hosts": {"irc": ["irc.example.test"]}}],
                    }
                ],
            }
        ],
    }

    parsed = parse_v2_document(source)
    assert parsed.service_instances[0].configuration_bindings[0].value == [
        {"id": "platform", "hosts": {"irc": ["irc.example.test"]}}
    ]

    source["service_instances"][0]["configuration_bindings"][0]["value"].append(
        {"id": "platform", "hosts": {"irc": ["other.example.test"]}}
    )
    with pytest.raises(ValueError, match="duplicate identity"):
        parse_v2_document(source)


def test_configuration_binding_schema_rejects_untyped_object_value() -> None:
    schema_path = Path(__file__).parents[2] / "src/infralink/schemas/observation/v2/document.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    binding_schema = schema["$defs"]["ConfigurationBinding"]

    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(binding_schema).validate(
            {"slot_id": "datasource", "value": {"nested": {"untyped": "object"}}}
        )


@pytest.mark.parametrize(
    ("configuration_bindings", "message"),
    [
        ([{"slot_id": "unknown", "value": "value"}], "unknown configuration slot"),
        ([{"slot_id": "tenant-stacks", "value": [{"id": "platform"}]}], "required"),
        (
            [
                {"slot_id": "tenant-stacks", "value": []},
                {"slot_id": "tenant-stacks", "value": []},
            ],
            "duplicate configuration binding",
        ),
    ],
)
def test_profile_configuration_slots_reject_invalid_instance_bindings(
    configuration_bindings: list[dict[str, object]], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        parse_v2_document(
            {
                "schema_version": "infralink.observation/v2",
                "service_profiles": [
                    {
                        "id": "irc-stack",
                        "components": [{"id": "inspircd", "endpoints": []}],
                        "configuration_slots": [
                            {
                                "id": "tenant-stacks",
                                "component_id": "inspircd",
                                "kind": "record-list",
                                "identity_field": "id",
                                "purpose": "Declare IRC tenant stack identity and hostnames.",
                                "fields": [
                                    {"id": "id", "kind": "string"},
                                    {"id": "redis-database", "kind": "integer"},
                                ],
                            }
                        ],
                    }
                ],
                "service_instances": [
                    {
                        "id": "staging",
                        "host_id": HOST_ID,
                        "profile_id": "irc-stack",
                        "components": [{"slot_id": "inspircd"}],
                        "configuration_bindings": configuration_bindings,
                    }
                ],
            }
        )


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
                            "endpoint_overrides": [
                                {
                                    "endpoint_id": "metrics",
                                    "address": "203.0.113.10",
                                    "port": 9154,
                                    "exposure": "public",
                                }
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
        "203.0.113.10",
        9154,
    )
    assert projection.prometheus.path == "/metrics"
    assert projection.prometheus.labels == {"environment": "production"}
    assert projection.gatus.endpoint_id == f"{HOST_ID}/edge/nginx/metrics"
    assert (projection.gatus.protocol, projection.gatus.address, projection.gatus.port) == (
        "http",
        "203.0.113.10",
        9154,
    )
    assert projection.gatus.path == "/metrics"
    assert projection.grafana.metric_name == "nginx_http_requests_total"
    assert projection.grafana.unit == "requests"
    assert projection.grafana.labels == {"environment": "production"}
    assert projection.doctor.required is True
    assert projection.doctor.query == "sum(nginx_http_requests_total)"
    assert projection.doctor.operator == "gte"
    assert projection.doctor.threshold == 0
    assert projection.prometheus.address == "203.0.113.10"
    assert projection.prometheus.port == 9154
    assert projection.gatus.address == "203.0.113.10"
    assert projection.gatus.port == 9154
    assert {item.prometheus.address for item in projections} == {"203.0.113.10", "100.64.0.11"}


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
