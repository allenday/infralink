import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import cli
from infralink.cli.queries import (
    edge_summary,
    host_summary,
    list_apps,
    list_edges,
    list_hosts,
    list_services,
    show_app,
    show_edge,
    show_host,
    show_service,
)
from infralink.core.application import ApplicationSet
from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry

ROOT = Path(__file__).resolve().parents[1]
HOST_A = "11111111-1111-4111-8111-111111111111"
HOST_B = "22222222-2222-4222-8222-222222222222"
HOST_C = "33333333-3333-4333-8333-333333333333"
HOST_D = "44444444-4444-4444-8444-444444444444"
EDGE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EDGE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EDGE_C = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
EDGE_D = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


@pytest.fixture
def edges() -> EdgeSet:
    return EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": EDGE_B,
                    "type": "api",
                    "from": {"hosts": [HOST_A], "service": "worker"},
                    "to": {"host": HOST_B, "service": "api", "port": 443},
                    "protocol": "https",
                },
                {
                    "id": EDGE_A,
                    "type": "database",
                    "from": {"hosts": [HOST_B], "service": "api"},
                    "to": {"host": HOST_A, "service": "postgres", "port": 5432},
                    "protocol": "postgresql",
                    "auth": {"type": "password", "secret_ref": "safe-ref-name"},
                },
                {
                    "id": EDGE_C,
                    "type": "api",
                    "from": {"hosts": [HOST_A], "service": "worker"},
                    "to": {"host": HOST_C, "service": "api", "port": 7777},
                    "protocol": "external",
                },
                {
                    "id": EDGE_D,
                    "type": "api",
                    "from": {"hosts": [HOST_A], "service": "worker"},
                    "to": {"host": HOST_B, "service": "api"},
                    "protocol": "no-port",
                },
            ]
        }
    )


@pytest.fixture
def registry() -> Registry:
    apps = ApplicationSet.from_dict(
        {
            "applications": {
                "test-app": {
                    "members": [
                        {"host": HOST_A, "services": ["worker", "postgres"]},
                        {"host": HOST_B, "services": ["api", "member-only"]},
                        {"host": HOST_D, "services": []},
                    ],
                    "edges": [EDGE_B, EDGE_C, EDGE_D, "missing-edge-id"],
                }
            }
        }
    )
    data = {
        "hosts": {
            HOST_B: {
                "canonical_name": "z-host",
                "status": "active",
                "projects": ["zeta", "alpha"],
                "roles": ["api", "role-only"],
                "services": {"api": {"port": 8443, "protocol": "https"}},
            },
            HOST_A: {
                "canonical_name": "a-host",
                "status": "active",
                "projects": ["core"],
                "roles": ["worker"],
                "services": {
                    "postgres": {"port": 5432, "protocol": "postgresql"},
                    "worker": {"port": 9000, "protocol": "http"},
                },
            },
            HOST_C: {
                "canonical_name": "nonmember-host",
                "status": "active",
                "services": {"api": {"port": 9999, "protocol": "nonmember"}},
            },
            HOST_D: {
                "canonical_name": "empty-member",
                "status": "active",
                "services": {"api": {"port": 5555, "protocol": "empty-member"}},
            },
        }
    }
    loaded = Registry.from_dict(data)
    return Registry(
        {host.uuid: host for host in loaded},
        applications=apps,
    )


def test_host_summary_truncates_sorted_relationship_preview(registry: Registry) -> None:
    host = registry.get(HOST_A)
    assert host is not None
    summary = host_summary(host, service_preview_limit=1)

    assert summary.service_count == 2
    assert summary.services == ["postgres"]
    assert summary.services_truncated is True


def test_all_lists_are_complete_and_stably_sorted(registry: Registry, edges: EdgeSet) -> None:
    assert list_hosts(registry).items == [HOST_A, HOST_B, HOST_C, HOST_D]
    assert list_edges(edges).items == [EDGE_A, EDGE_B, EDGE_C, EDGE_D]
    assert list_services(registry, edges).items == sorted(list_services(registry, edges).items)
    assert {"api", "postgres", "role-only"} <= set(list_services(registry, edges).items)
    assert list_apps(registry, edges).items == ["test-app"]


def test_service_summary_aggregates_roles_services_and_edge_targets(
    registry: Registry, edges: EdgeSet
) -> None:
    services = show_service(registry, edges, "api").service

    assert services.host_ids == [HOST_B, HOST_C, HOST_D]
    assert services.ports == [443, 5555, 7777, 8443, 9999]
    assert services.protocols == [
        "empty-member",
        "external",
        "https",
        "no-port",
        "nonmember",
    ]


def test_edge_summary_contains_only_safe_declared_fields(edges: EdgeSet) -> None:
    edge = edges.get(EDGE_A)
    assert edge is not None
    summary = edge_summary(edge)
    dumped = summary.model_dump(mode="json", by_alias=True)

    assert dumped["from"] == {"hosts": [HOST_B], "service": "api"}
    assert dumped["to"] == {"host": HOST_A, "service": "postgres", "port": 5432}
    assert dumped["secret_refs"] == ["safe-ref-name"]
    assert "auth" not in dumped


def test_missing_edge_ports_remain_truthful_across_public_queries(
    registry: Registry, edges: EdgeSet
) -> None:
    listed_edge_ids = list_edges(edges).items
    listed_service_ids = list_services(registry, edges).items
    shown_service = show_service(registry, edges, "api")
    shown_app = show_app(registry, edges, "test-app")
    app_edge = next(item for item in shown_app.edges.items if item.id == EDGE_D)

    assert EDGE_D in listed_edge_ids
    assert "api" in listed_service_ids
    assert 443 in shown_service.service.ports
    assert all(type(port) is int for port in shown_service.service.ports)
    assert shown_service.ports.items == shown_service.service.ports
    assert app_edge.to == {"host": HOST_B, "service": "api"}
    assert all(type(port) is int for service in shown_app.services.items for port in service.ports)


def test_detail_queries_return_complete_typed_pages(registry: Registry, edges: EdgeSet) -> None:
    host = show_host(registry, HOST_A, collection="services", limit=100)
    service = show_service(registry, edges, "api", collection="hosts", limit=100)
    edge = show_edge(edges, EDGE_A, limit=100)
    app = show_app(registry, edges, "test-app", collection="services", limit=100)

    assert host.host.id == HOST_A
    assert host.services.page.total == 2
    assert service.service.id == "api"
    assert service.hosts.items == [HOST_B, HOST_C, HOST_D]
    assert [item.id for item in service.edges.items] == [EDGE_A, EDGE_B, EDGE_C, EDGE_D]
    assert edge.edge.id == EDGE_A
    assert edge.secret_refs.items == ["safe-ref-name"]
    assert app.app.edge_count == 3
    assert [item.id for item in app.edges.items] == [EDGE_B, EDGE_C, EDGE_D]
    app_services = {item.id: item for item in app.services.items}
    assert app_services["member-only"].host_ids == [HOST_B]
    assert app_services["api"].host_ids == [HOST_B]
    assert app_services["api"].ports == [443, 8443]
    assert app_services["api"].protocols == ["https", "no-port"]
    assert "api" not in {
        service_id
        for member in registry.applications.get_application("test-app").schema.members
        if member.host == HOST_D
        for service_id in member.services
    }


@pytest.mark.parametrize(
    ("call", "entity_type"),
    [
        (lambda registry, edges: show_host(registry, "missing"), "host"),
        (lambda registry, edges: show_service(registry, edges, "missing"), "service"),
        (lambda registry, edges: show_edge(edges, "missing"), "edge"),
        (lambda registry, edges: show_app(registry, edges, "missing"), "app"),
    ],
)
def test_missing_entities_use_shared_not_found_error(
    registry: Registry, edges: EdgeSet, call, entity_type: str
) -> None:
    with pytest.raises(CliFailure) as error:
        call(registry, edges)
    assert error.value.code == ErrorCode.ENTITY_NOT_FOUND
    assert error.value.exit_code == 3
    assert error.value.details["entity_type"] == entity_type


def _write_cli_inputs(
    tmp_path: Path, host_count: int = 2, *, with_edge: bool = False
) -> tuple[Path, Path]:
    hosts = {
        f"00000000-0000-4000-8000-{index:012d}": {
            "canonical_name": f"host-{index:04d}",
            "status": "active",
            "services": {f"service-{index:04d}": {"port": 8000 + index, "protocol": "http"}},
        }
        for index in range(host_count)
    }
    if host_count <= 2:
        hosts["00000000-0000-4000-8000-000000000000"]["services"]["service-extra"] = {
            "port": 9000,
            "protocol": "http",
        }
    registry_path = tmp_path / "registry.yml"
    edges_path = tmp_path / "edges.yml"
    registry_path.write_text(json.dumps({"hosts": hosts}))
    (tmp_path / "applications.yml").write_text(
        json.dumps(
            {
                "applications": {
                    "test-app": {
                        "members": [
                            {
                                "host": "00000000-0000-4000-8000-000000000000",
                                "services": ["service-0000"],
                            }
                        ],
                        "edges": [EDGE_A] if with_edge else [],
                    }
                }
            }
        )
    )
    edge_items = (
        [
            {
                "id": EDGE_A,
                "type": "api",
                "from": {
                    "hosts": ["00000000-0000-4000-8000-000000000000"],
                    "service": "service-0000",
                },
                "to": {
                    "host": "00000000-0000-4000-8000-000000000001",
                    "service": "service-0001",
                    "port": 443,
                },
                "protocol": "https",
            }
        ]
        if with_edge
        else []
    )
    edges_path.write_text(json.dumps({"edges": edge_items}))
    return registry_path, edges_path


def _invoke(registry_path: Path, edges_path: Path, *args: str):
    if registry_path.is_file():
        registry_path, edges_path = _app_checkout(registry_path, edges_path)
    return CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(registry_path),
            "--edges",
            str(edges_path),
            *args,
        ],
    )


def _app_checkout(registry_path: Path, edges_path: Path) -> tuple[Path, Path]:
    """Materialize the public app fixture through the checkout-root contract."""
    checkout = registry_path.parent / "app-checkout"
    hosts = json.loads(registry_path.read_text())["hosts"]
    for host_id, manifest in hosts.items():
        path = checkout / "hosts" / host_id / "manifest.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"hosts": {host_id: manifest}}))
    applications = registry_path.parent / "applications.yml"
    (checkout / "hosts").mkdir(parents=True, exist_ok=True)
    if applications.is_file():
        (checkout / "hosts" / "applications.yml").write_text(applications.read_text())
    checkout_edges = checkout / "network/main-dev/edges/edges.yml"
    checkout_edges.parent.mkdir(parents=True, exist_ok=True)
    checkout_edges.write_text(edges_path.read_text())
    return checkout, checkout_edges


def _payload(result) -> dict:
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return json.loads(result.output)


@pytest.mark.parametrize(
    ("args", "schema_name"),
    [
        (("hosts",), "hosts"),
        (("services",), "services"),
        (("edges-list",), "edges-list"),
        (("host", "show", "00000000-0000-4000-8000-000000000000"), "host-show"),
        (("service", "show", "service-0000"), "service-show"),
        (("app", "list"), "app-list"),
        (("app", "show", "test-app"), "app-show"),
    ],
)
def test_cli_query_results_match_existing_schemas(
    tmp_path: Path, args: tuple[str, ...], schema_name: str
) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path)
    result = _invoke(registry_path, edges_path, *args)
    payload = _payload(result)
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1" / f"{schema_name}.json").read_text())

    assert result.exit_code == 0
    Draft202012Validator(schema).validate(payload)


def test_cli_edge_detail_matches_existing_schema(tmp_path: Path) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path, with_edge=True)
    result = _invoke(registry_path, edges_path, "edge", "show", EDGE_A)
    payload = _payload(result)
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/edge-show.json").read_text())

    assert result.exit_code == 0
    Draft202012Validator(schema).validate(payload)


def test_cli_inspection_advertises_scoped_direct_health_actions(tmp_path: Path) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path, with_edge=True)

    edge_payload = _payload(_invoke(registry_path, edges_path, "edge", "show", EDGE_A))
    service_payload = _payload(
        _invoke(registry_path, edges_path, "service", "show", "service-0001")
    )

    edge_action = next(item for item in edge_payload["next_actions"] if item["rel"] == "check")
    service_action = next(
        item for item in service_payload["next_actions"] if item["rel"] == "check"
    )
    assert edge_action["command"].endswith(f"check --edge {EDGE_A}")
    assert service_payload["result"]["edges"]["items"] == [
        {
            "id": EDGE_A,
            "type": "api",
            "from": {
                "hosts": ["00000000-0000-4000-8000-000000000000"],
                "service": "service-0000",
            },
            "to": {
                "host": "00000000-0000-4000-8000-000000000001",
                "service": "service-0001",
                "port": 443,
            },
            "protocol": "https",
            "secret_ref_count": 0,
            "secret_refs": [],
            "secret_refs_truncated": False,
        }
    ]
    assert service_action["command"] == edge_action["command"]


def test_detail_cursor_requires_collection_and_only_advances_selected_page(
    tmp_path: Path,
) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path)
    host_id = "00000000-0000-4000-8000-000000000000"
    first = _payload(_invoke(registry_path, edges_path, "host", "show", host_id, "--limit", "1"))
    cursor = first["result"]["services"]["page"]["next_cursor"]
    assert cursor is not None

    ambiguous_result = _invoke(
        registry_path,
        edges_path,
        "host",
        "show",
        host_id,
        "--cursor",
        cursor,
    )
    ambiguous = _payload(ambiguous_result)
    assert ambiguous_result.exit_code == 2
    assert ambiguous["error"]["code"] == "invalid_cursor"

    resumed = _payload(
        _invoke(
            registry_path,
            edges_path,
            "host",
            "show",
            host_id,
            "--collection",
            "services",
            "--cursor",
            cursor,
            "--limit",
            "1",
        )
    )
    assert resumed["result"]["services"]["items"] == ["service-extra"]
    assert resumed["result"]["projects"]["page"]["returned"] == 0


def test_cli_missing_entity_is_one_safe_json_document(tmp_path: Path) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path)
    result = _invoke(registry_path, edges_path, "edge", "show", "canary-missing")
    payload = _payload(result)

    assert result.exit_code == 3
    assert payload["error"]["code"] == "entity_not_found"
    assert payload["error"]["details"] == {
        "entity_type": "edge",
        "requested_id": "canary-missing",
    }


def test_truncated_host_preview_has_executable_detail_action(
    tmp_path: Path,
) -> None:
    host_id = "00000000-0000-4000-8000-000000000000"
    registry_path = tmp_path / "registry.yml"
    edges_path = tmp_path / "edges.yml"
    registry_path.write_text(
        json.dumps(
            {
                "hosts": {
                    host_id: {
                        "canonical_name": "large-host",
                        "status": "active",
                        "projects": [f"project-{index:03d}" for index in range(65)],
                        "services": {
                            f"service-{index:03d}": {"port": 10000 + index} for index in range(129)
                        },
                    }
                }
            }
        )
    )
    edges_path.write_text(json.dumps({"edges": []}))
    payload = _payload(_invoke(registry_path, edges_path, "hosts"))
    assert payload["result"]["items"] == [host_id]
    detail = next(item for item in payload["next_actions"] if item["rel"] == "show")
    assert detail["command"].endswith("host show '{id}'")
    assert detail["bindings"]["id"]["source"] == "result.items[]"


def test_truncated_service_preview_has_executable_detail_action(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.yml"
    edges_path = tmp_path / "edges.yml"
    hosts = {
        f"00000000-0000-4000-8000-{index:012d}": {
            "canonical_name": f"host-{index:03d}",
            "status": "active",
            "services": {
                "shared": {
                    "port": 10000 + index,
                    "protocol": f"protocol-{index:03d}",
                }
            },
        }
        for index in range(129)
    }
    registry_path.write_text(json.dumps({"hosts": hosts}))
    edges_path.write_text(json.dumps({"edges": []}))
    payload = _payload(_invoke(registry_path, edges_path, "services"))
    assert payload["result"]["items"] == ["shared"]
    detail = next(item for item in payload["next_actions"] if item["rel"] == "show")
    assert detail["command"].endswith("service show '{id}'")
    assert detail["bindings"]["id"]["source"] == "result.items[]"


def test_app_truncated_service_action_preserves_scope_and_paginates(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.yml"
    edges_path = tmp_path / "edges.yml"
    hosts = {
        f"00000000-0000-4000-8000-{index:012d}": {
            "canonical_name": f"member-{index:03d}",
            "status": "active",
            "services": {"shared": {"port": 10000 + index, "protocol": "http"}},
        }
        for index in range(129)
    }
    registry_path.write_text(json.dumps({"hosts": hosts}))
    edges_path.write_text(json.dumps({"edges": []}))
    (tmp_path / "applications.yml").write_text(
        json.dumps(
            {
                "applications": {
                    "large-app": {
                        "members": [{"host": host_id, "services": ["shared"]} for host_id in hosts]
                    }
                }
            }
        )
    )

    app_payload = _payload(_invoke(registry_path, edges_path, "app", "show", "large-app"))
    summary = app_payload["result"]["services"]["items"][0]
    assert summary["host_count"] == 129
    assert summary["hosts_truncated"] is True
