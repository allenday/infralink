"""Derive provider-neutral secret references from declared topology."""

from __future__ import annotations

from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.core.schema import SAFE_SECRET_REF_PATTERN
from infralink.secrets.base import SecretReference


def collect_secret_references(
    registry: Registry,
    edges: EdgeSet,
) -> list[SecretReference]:
    """Collect declared edge secret references without provider access."""
    grouped: dict[tuple[str, tuple[str, ...]], set[str]] = {}
    for edge in edges:
        if edge.secret_ref is None:
            continue
        if SAFE_SECRET_REF_PATTERN.fullmatch(edge.secret_ref) is None:
            raise ValueError("Topology contains an unsafe secret reference")
        target = registry.get_by_uuid(edge.target_host)
        projects = target.bws_projects if target else ()
        grouped.setdefault((edge.secret_ref, projects), set()).add(
            f"edges.{edge.id}.auth.secret_ref"
        )

    sorted_keys = sorted(
        grouped,
        key=lambda item: (item[0], item[1]),
    )
    return [
        SecretReference(
            ref=ref,
            projects=projects,
            locations=tuple(sorted(grouped[(ref, projects)])),
        )
        for ref, projects in sorted_keys
    ]
