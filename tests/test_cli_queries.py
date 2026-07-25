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
EDGE_A = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
EDGE_B = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


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
                    ],
                    "edges": [EDGE_B, "missing-edge-id"],
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


def test_all_lists_are_stably_sorted_and_bounded(registry: Registry, edges: EdgeSet) -> None:
    assert [item.id for item in list_hosts(registry, limit=1).items] == [HOST_A]
    assert [item.id for item in list_edges(edges, limit=1).items] == [EDGE_A]
    assert list_hosts(registry, limit=1).page.total == 2
    assert list_services(registry, edges, limit=1).page.returned == 1
    assert [item.id for item in list_apps(registry, edges, limit=10).items] == ["test-app"]


def test_service_summary_aggregates_roles_services_and_edge_targets(
    registry: Registry, edges: EdgeSet
) -> None:
    services = {item.id: item for item in list_services(registry, edges, limit=100).items}

    assert services["role-only"].host_ids == [HOST_B]
    assert services["postgres"].host_ids == [HOST_A]
    assert services["postgres"].ports == [5432]
    assert services["api"].host_ids == [HOST_B]
    assert services["api"].ports == [443, 8443]
    assert services["api"].protocols == ["https"]


def test_edge_summary_contains_only_safe_declared_fields(edges: EdgeSet) -> None:
    edge = edges.get(EDGE_A)
    assert edge is not None
    summary = edge_summary(edge)
    dumped = summary.model_dump(mode="json", by_alias=True)

    assert dumped["from"] == {"hosts": [HOST_B], "service": "api"}
    assert dumped["to"] == {"host": HOST_A, "service": "postgres", "port": 5432}
    assert dumped["secret_refs"] == ["safe-ref-name"]
    assert "auth" not in dumped


def test_detail_queries_return_complete_typed_pages(registry: Registry, edges: EdgeSet) -> None:
    host = show_host(registry, HOST_A, collection="services", limit=100)
    service = show_service(registry, edges, "api", collection="hosts", limit=100)
    edge = show_edge(edges, EDGE_A, limit=100)
    app = show_app(registry, edges, "test-app", collection="services", limit=100)

    assert host.host.id == HOST_A
    assert host.services.page.total == 2
    assert service.service.id == "api"
    assert service.hosts.items == [HOST_B]
    assert edge.edge.id == EDGE_A
    assert edge.secret_refs.items == ["safe-ref-name"]
    assert app.app.edge_count == 1
    assert [item.id for item in app.edges.items] == [EDGE_B]
    app_services = {item.id: item for item in app.services.items}
    assert app_services["member-only"].host_ids == [HOST_B]


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
    return CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry_path),
            "--edges",
            str(edges_path),
            *args,
        ],
    )


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


def test_cli_cursor_resumes_without_duplicates_and_exposes_exact_binding(
    tmp_path: Path,
) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path, host_count=3)
    first_result = _invoke(registry_path, edges_path, "hosts", "--limit", "2")
    first = _payload(first_result)
    cursor = first["result"]["page"]["next_cursor"]

    assert first["meta"]["truncated"] is True
    continuation = next(item for item in first["next_actions"] if item["rel"] == "continue")
    assert continuation["argv"] == [
        "infralink",
        "hosts",
        "--collection",
        "items",
        "--cursor",
        "{cursor}",
        "--limit",
        "2",
    ]
    assert continuation["bindings"]["cursor"]["source"] == "result.page.next_cursor"

    second = _payload(
        _invoke(registry_path, edges_path, "hosts", "--limit", "2", "--cursor", cursor)
    )
    first_ids = {item["id"] for item in first["result"]["items"]}
    second_ids = {item["id"] for item in second["result"]["items"]}
    assert first_ids.isdisjoint(second_ids)
    assert second["result"]["page"]["returned"] == 1
    assert second["meta"]["truncated"] is False


def test_services_over_default_limit_are_paginated(tmp_path: Path) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path, host_count=1005)
    payload = _payload(_invoke(registry_path, edges_path, "services"))

    assert len(payload["result"]["items"]) == 100
    assert payload["result"]["page"]["total"] == 1005
    assert payload["result"]["page"]["next_cursor"]
    assert payload["meta"]["truncated"] is True


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


def test_cli_rejects_tampered_mismatched_and_stale_cursors(tmp_path: Path) -> None:
    registry_path, edges_path = _write_cli_inputs(tmp_path, host_count=3)
    first = _payload(_invoke(registry_path, edges_path, "hosts", "--limit", "1"))
    cursor = first["result"]["page"]["next_cursor"]
    assert cursor is not None

    payload_segment, signature = cursor.split(".")
    tampered = f"{payload_segment[:-1]}A.{signature}"
    for args in [
        ("hosts", "--cursor", tampered),
        ("services", "--cursor", cursor),
    ]:
        result = _invoke(registry_path, edges_path, *args)
        payload = _payload(result)
        assert result.exit_code == 2
        assert payload["error"]["code"] == "invalid_cursor"

    data = json.loads(registry_path.read_text())
    data["hosts"]["00000000-0000-4000-8000-999999999999"] = {
        "canonical_name": "new-host",
        "status": "active",
    }
    registry_path.write_text(json.dumps(data))
    stale_result = _invoke(registry_path, edges_path, "hosts", "--cursor", cursor)
    stale = _payload(stale_result)
    assert stale_result.exit_code == 2
    assert stale["error"]["code"] == "invalid_cursor"


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
