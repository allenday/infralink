"""Tests for provider-neutral secret inventory collection."""

from __future__ import annotations

import copy

import infralink
from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.secrets import SecretAudit, SecretReference, SecretResolver, SecretValue

EDGE_A = "11111111-1111-4111-8111-111111111111"
EDGE_Z = "99999999-9999-4999-8999-999999999999"
EDGE_PROJECT_B = "22222222-2222-4222-8222-222222222222"
EDGE_NO_PROJECT = "33333333-3333-4333-8333-333333333333"
EDGE_MISSING_HOST = "44444444-4444-4444-8444-444444444444"
EDGE_WITHOUT_SECRET = "55555555-5555-4555-8555-555555555555"
EDGE_SHARED_NO_PROJECT = "66666666-6666-4666-8666-666666666666"
EDGE_HIERARCHICAL = "77777777-7777-4777-8777-777777777777"


def _edge(
    edge_id: str,
    target: str,
    secret_ref: str | None,
) -> dict[str, object]:
    auth = {"type": "password", "secret_ref": secret_ref} if secret_ref else {}
    return {
        "id": edge_id,
        "type": "database",
        "from": {"hosts": [], "service": "app"},
        "to": {"host": target, "service": "postgresql", "port": 5432},
        "auth": auth,
    }


def test_top_level_package_exports_provider_neutral_secret_contracts() -> None:
    assert infralink.SecretValue is SecretValue
    assert infralink.SecretReference is SecretReference
    assert infralink.SecretAudit is SecretAudit
    assert infralink.SecretResolver is SecretResolver


def test_host_exposes_read_only_bws_configuration_with_defensive_tuple() -> None:
    registry = Registry.from_dict(
        {
            "hosts": {
                "host-a": {
                    "canonical_name": "database",
                    "bws_project": "primary",
                    "bws_machine_account": "machine-account",
                    "bws_extra_projects": ["shared", "archive"],
                }
            }
        }
    )

    host = registry.get_by_uuid("host-a")

    assert host is not None
    assert host.bws_project == "primary"
    assert host.bws_machine_account == "machine-account"
    assert host.bws_extra_projects == ("shared", "archive")
    assert type(host).bws_project.fset is None


def test_collect_secret_references_groups_deduplicates_and_sorts() -> None:
    from infralink.secrets.inventory import collect_secret_references

    registry = Registry.from_dict(
        {
            "hosts": {
                "host-a": {
                    "canonical_name": "database-a",
                    "bws_project": "project-a",
                },
                "host-b": {
                    "canonical_name": "database-b",
                    "bws_project": "project-b",
                },
                "host-c": {"canonical_name": "database-c"},
            }
        }
    )
    edges = EdgeSet.from_dict(
        {
            "edges": [
                _edge(EDGE_Z, "host-a", "shared-ref"),
                _edge(EDGE_A, "host-a", "shared-ref"),
                _edge(EDGE_A, "host-a", "shared-ref"),
                _edge(EDGE_PROJECT_B, "host-b", "shared-ref"),
                _edge(EDGE_NO_PROJECT, "host-c", "alpha-ref"),
                _edge(EDGE_MISSING_HOST, "missing", "missing-ref"),
                _edge(EDGE_SHARED_NO_PROJECT, "missing", "shared-ref"),
                _edge(EDGE_HIERARCHICAL, "host-a", "production/db-password"),
                _edge(EDGE_WITHOUT_SECRET, "host-a", None),
            ]
        }
    )
    before_registry = [host.to_dict() for host in registry]
    before_edges = [edge.to_dict() for edge in edges]

    references = collect_secret_references(registry, edges)

    assert references == [
        SecretReference(
            ref="alpha-ref",
            project=None,
            locations=(f"edges.{EDGE_NO_PROJECT}.auth.secret_ref",),
        ),
        SecretReference(
            ref="missing-ref",
            project=None,
            locations=(f"edges.{EDGE_MISSING_HOST}.auth.secret_ref",),
        ),
        SecretReference(
            ref="production/db-password",
            project="project-a",
            locations=(f"edges.{EDGE_HIERARCHICAL}.auth.secret_ref",),
        ),
        SecretReference(
            ref="shared-ref",
            project=None,
            locations=(f"edges.{EDGE_SHARED_NO_PROJECT}.auth.secret_ref",),
        ),
        SecretReference(
            ref="shared-ref",
            project="project-a",
            locations=(
                f"edges.{EDGE_A}.auth.secret_ref",
                f"edges.{EDGE_Z}.auth.secret_ref",
            ),
        ),
        SecretReference(
            ref="shared-ref",
            project="project-b",
            locations=(f"edges.{EDGE_PROJECT_B}.auth.secret_ref",),
        ),
    ]
    assert all(reference.required for reference in references)
    assert [host.to_dict() for host in registry] == before_registry
    assert [edge.to_dict() for edge in edges] == before_edges


def test_collect_secret_references_does_not_share_mutable_locations() -> None:
    from infralink.secrets.inventory import collect_secret_references

    registry = Registry.from_dict({"hosts": {}})
    edges = EdgeSet.from_dict({"edges": [_edge(EDGE_A, "missing", "ref")]})

    first = collect_secret_references(registry, edges)
    copied = copy.deepcopy(first)

    assert first == copied
    assert first[0].locations == (f"edges.{EDGE_A}.auth.secret_ref",)


def test_inventory_reports_token_and_certificate_references() -> None:
    from infralink.secrets.inventory import collect_secret_references

    registry = Registry.from_dict(
        {"hosts": {"host-a": {"canonical_name": "target", "bws_project": "project-a"}}}
    )
    edges = EdgeSet.from_dict(
        {
            "edges": [
                {
                    **_edge(EDGE_A, "host-a", None),
                    "auth": {
                        "type": "token",
                        "secret_ref": "production/api-token",
                    },
                },
                {
                    **_edge(EDGE_Z, "host-a", None),
                    "auth": {
                        "type": "certificate",
                        "secret_ref": "production/client-cert",
                    },
                },
            ]
        }
    )

    references = collect_secret_references(registry, edges)

    assert [reference.ref for reference in references] == [
        "production/api-token",
        "production/client-cert",
    ]
