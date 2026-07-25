"""Derive provider-neutral secret references from declared topology."""

from __future__ import annotations

from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.secrets.base import SecretReference


def collect_secret_references(
    registry: Registry,
    edges: EdgeSet,
) -> list[SecretReference]:
    """Collect declared edge secret references without provider access."""
    grouped: dict[tuple[str, str | None], set[str]] = {}
    for edge in edges:
        if not edge.secret_ref:
            continue
        target = registry.get_by_uuid(edge.target_host)
        project = target.bws_project if target else None
        grouped.setdefault((edge.secret_ref, project), set()).add(
            f"edges.{edge.id}.auth.secret_ref"
        )

    sorted_keys = sorted(
        grouped,
        key=lambda item: (item[0], item[1] is not None, item[1] or ""),
    )
    return [
        SecretReference(
            ref=ref,
            project=project,
            locations=tuple(sorted(grouped[(ref, project)])),
        )
        for ref, project in sorted_keys
    ]
