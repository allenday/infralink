"""Focused regression tests for generated topology artifacts."""

import infralink.generators as generators
from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.generators.markdown import generate_edge_index, generate_host_doc
from infralink.generators.mermaid import generate_mermaid

SOURCE_UUID = "11111111-1111-4111-8111-111111111111"
TARGET_UUID = "22222222-2222-4222-8222-222222222222"


def _registry(services: dict[str, dict[str, object]] | None = None) -> Registry:
    return Registry.from_dict(
        {
            "hosts": {
                SOURCE_UUID: {
                    "canonical_name": "source",
                    "services": services or {"worker": {}},
                },
                TARGET_UUID: {
                    "canonical_name": "target",
                    "services": {"postgres": {}, "redis": {}},
                },
            }
        }
    )


def _edge(
    edge_id: str,
    target_service: str,
    target_port: int,
    *,
    source_service: str = "worker",
) -> dict[str, object]:
    return {
        "id": edge_id,
        "type": "database",
        "from": {"hosts": [SOURCE_UUID], "service": source_service},
        "to": {
            "host": TARGET_UUID,
            "service": target_service,
            "port": target_port,
        },
    }


def test_mermaid_deduplicates_only_identical_four_part_edges() -> None:
    registry = _registry()
    repeated = _edge("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "postgres", 5432)
    edges = EdgeSet.from_dict(
        {
            "edges": [
                repeated,
                _edge("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "postgres", 5432),
                _edge(
                    "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
                    "postgres",
                    5432,
                    source_service="scheduler",
                ),
                _edge("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "redis", 6379),
            ]
        }
    )

    diagram = generate_mermaid(list(registry), edges, registry)

    assert diagram.count("11111111_worker --> 22222222_postgres") == 1
    assert diagram.count("11111111_scheduler --> 22222222_postgres") == 1
    assert diagram.count("11111111_worker --> 22222222_redis") == 1


def test_edge_index_is_available_from_public_generator_package() -> None:
    assert generators.generate_edge_index is generate_edge_index


def test_host_doc_service_rows_are_deterministic_for_mapping_order() -> None:
    first_registry = _registry({"zeta": {}, "alpha": {}})
    second_registry = _registry({"alpha": {}, "zeta": {}})
    edges = EdgeSet([])
    first_host = first_registry.get_by_uuid(SOURCE_UUID)
    second_host = second_registry.get_by_uuid(SOURCE_UUID)
    assert first_host is not None
    assert second_host is not None

    first = generate_host_doc(first_host, edges, first_registry)
    second = generate_host_doc(second_host, edges, second_registry)

    assert first == second
    assert first.index("| alpha | Active |") < first.index("| zeta | Active |")
