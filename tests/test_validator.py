"""Tests for validator cross-checks."""

from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.core.validator import validate_edges_against_registry


def build_registry(hosts: dict[str, dict]) -> Registry:
    return Registry.from_dict({"hosts": hosts})


def test_cross_validation_happy_path():
    registry = build_registry(
        {
            "11111111-2222-3333-4444-555555555555": {
                "canonical_name": "app-1",
                "status": "active",
                "services": {"api": {"port": 8080}},
            },
            "66666666-7777-8888-9999-000000000000": {
                "canonical_name": "db-1",
                "status": "active",
                "services": {"postgresql": {"port": 5432}},
            },
        }
    )

    edges = EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                    "type": "database",
                    "from": {
                        "hosts": ["11111111-2222-3333-4444-555555555555"],
                        "service": "api",
                    },
                    "to": {
                        "host": "66666666-7777-8888-9999-000000000000",
                        "service": "postgresql",
                        "port": 5432,
                    },
                }
            ]
        }
    )

    result = validate_edges_against_registry(registry, edges)

    assert result.errors == []
    assert result.warnings == []


def test_missing_target_host():
    registry = build_registry(
        {
            "11111111-2222-3333-4444-555555555555": {
                "canonical_name": "app-1",
                "status": "active",
                "services": {"api": {"port": 8080}},
            }
        }
    )

    edges = EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": "bbbbbbbb-cccc-dddd-eeee-ffffffffffff",
                    "type": "api",
                    "from": {"hosts": ["11111111-2222-3333-4444-555555555555"], "service": "api"},
                    "to": {
                        "host": "00000000-1111-2222-3333-444444444444",
                        "service": "unknown",
                        "port": 9000,
                    },
                }
            ]
        }
    )

    result = validate_edges_against_registry(registry, edges)

    assert any("target host not found" in err for err in result.errors)


def test_missing_target_service():
    registry = build_registry(
        {
            "66666666-7777-8888-9999-000000000000": {
                "canonical_name": "db-1",
                "status": "active",
                "services": {"postgresql": {"port": 5432}},
            }
        }
    )

    edges = EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": "cccccccc-dddd-eeee-ffff-000000000000",
                    "type": "api",
                    "from": {"hosts": [], "service": "api"},
                    "to": {
                        "host": "66666666-7777-8888-9999-000000000000",
                        "service": "redis",
                        "port": 6379,
                    },
                }
            ]
        }
    )

    result = validate_edges_against_registry(registry, edges)

    assert any("target service 'redis'" in err for err in result.errors)


def test_port_conflict_detection():
    registry = build_registry(
        {
            "66666666-7777-8888-9999-000000000000": {
                "canonical_name": "db-1",
                "status": "active",
                "services": {"postgresql": {"port": 5432}},
            }
        }
    )

    edges = EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": "dddddddd-eeee-ffff-1111-222222222222",
                    "type": "database",
                    "from": {"hosts": ["66666666-7777-8888-9999-000000000000"], "service": "api"},
                    "to": {
                        "host": "66666666-7777-8888-9999-000000000000",
                        "service": "postgresql",
                        "port": 55432,
                    },
                }
            ]
        }
    )

    result = validate_edges_against_registry(registry, edges)

    assert any("does not match service 'postgresql' port 5432" in err for err in result.errors)


def test_orphan_detection():
    registry = build_registry(
        {
            "11111111-2222-3333-4444-555555555555": {
                "canonical_name": "app-1",
                "status": "active",
                "services": {"api": {"port": 8080}},
            },
            "66666666-7777-8888-9999-000000000000": {
                "canonical_name": "db-1",
                "status": "active",
                "services": {"postgresql": {"port": 5432}},
            },
            "99999999-aaaa-bbbb-cccc-dddddddddddd": {
                "canonical_name": "lonely-host",
                "status": "active",
                "services": {"ssh": {"port": 22}},
            },
        }
    )

    edges = EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": "eeeeeeee-ffff-1111-2222-333333333333",
                    "type": "database",
                    "from": {"hosts": ["11111111-2222-3333-4444-555555555555"], "service": "api"},
                    "to": {
                        "host": "66666666-7777-8888-9999-000000000000",
                        "service": "postgresql",
                        "port": 5432,
                    },
                }
            ]
        }
    )

    result = validate_edges_against_registry(registry, edges)

    assert any("lonely-host" in warn for warn in result.warnings)


def test_missing_source_host():
    registry = build_registry(
        {
            "66666666-7777-8888-9999-000000000000": {
                "canonical_name": "db-1",
                "status": "active",
                "services": {"postgresql": {"port": 5432}},
            }
        }
    )

    edges = EdgeSet.from_dict(
        {
            "edges": [
                {
                    "id": "ffffffff-1111-2222-3333-444444444444",
                    "type": "database",
                    "from": {"hosts": ["11111111-2222-3333-4444-555555555555"], "service": "api"},
                    "to": {
                        "host": "66666666-7777-8888-9999-000000000000",
                        "service": "postgresql",
                        "port": 5432,
                    },
                }
            ]
        }
    )

    result = validate_edges_against_registry(registry, edges)

    assert any("source host not found" in err for err in result.errors)
