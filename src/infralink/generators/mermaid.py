"""Mermaid diagram generation."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infralink.core.edges import EdgeSet
    from infralink.core.registry import Host, Registry


def generate_mermaid(
    hosts: list[Host],
    edges: EdgeSet,
    registry: Registry,
) -> str:
    """
    Generate Mermaid flowchart diagram.

    Returns Markdown with embedded Mermaid diagram.
    """
    lines = ["# Infrastructure Topology", "", "```mermaid", "flowchart LR"]

    # Group hosts by group
    groups: dict[str, list[Host]] = defaultdict(list)
    for host in hosts:
        group = host.group or "other"
        groups[group].append(host)

    # Define subgraphs for each group
    for group, group_hosts in sorted(groups.items()):
        lines.append(f"    subgraph {group}")
        for host in sorted(group_hosts, key=lambda h: h.canonical_name):
            node_id = host.uuid[:8]
            # Escape special characters
            label = host.canonical_name.replace("-", "_")
            lines.append(f"        {node_id}[{label}]")
        lines.append("    end")

    # Add edges
    lines.append("")
    lines.append("    %% Connections")

    seen_edges: set[tuple[str, str]] = set()
    for edge in edges:
        if edge.is_wildcard_source():
            continue

        target_id = edge.target_host[:8]

        for source_uuid in edge.source_hosts:
            source_id = source_uuid[:8]

            # Skip if we've already drawn this edge
            edge_key = (source_id, target_id)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            # Check if source and target are in our host list
            source_in_list = any(h.uuid.startswith(source_id) for h in hosts)
            target_in_list = any(h.uuid.startswith(target_id) for h in hosts)

            if not (source_in_list and target_in_list):
                continue

            # Style based on criticality
            if edge.is_critical:
                arrow = "==>"
            else:
                arrow = "-->"

            label = edge.target_service
            lines.append(f"    {source_id} {arrow}|{label}| {target_id}")

    lines.append("```")
    lines.append("")
    lines.append("## Legend")
    lines.append("")
    lines.append("- `==>` Critical connection")
    lines.append("- `-->` Standard connection")

    return "\n".join(lines)
