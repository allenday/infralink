"""Graphviz DOT diagram generation."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infralink.core.edges import EdgeSet
    from infralink.core.registry import Host, Registry


def generate_dot(
    hosts: list[Host],
    edges: EdgeSet,
    registry: Registry,
) -> str:
    """
    Generate Graphviz DOT diagram.

    Can be rendered with: dot -Tpng infrastructure.dot -o infrastructure.png
    """
    lines = [
        "digraph Infrastructure {",
        "    rankdir=LR;",
        "    node [shape=box, style=filled, fillcolor=lightblue];",
        "    edge [fontsize=10];",
        "",
    ]

    # Group hosts
    groups: dict[str, list[Host]] = defaultdict(list)
    for host in hosts:
        group = host.group or "other"
        groups[group].append(host)

    # Define subgraphs for each group
    for group, group_hosts in sorted(groups.items()):
        lines.append(f"    subgraph cluster_{group} {{")
        lines.append(f'        label="{group}";')
        lines.append("        style=dashed;")
        lines.append("        color=gray;")
        lines.append("")

        for host in sorted(group_hosts, key=lambda h: h.canonical_name):
            node_id = f"n_{host.uuid[:8]}"
            label = host.canonical_name

            # Color based on status
            if host.is_active:
                fillcolor = "lightblue"
            else:
                fillcolor = "lightgray"

            lines.append(f'        {node_id} [label="{label}", fillcolor={fillcolor}];')

        lines.append("    }")
        lines.append("")

    # Add edges
    lines.append("    // Connections")
    seen_edges: set[tuple[str, str]] = set()

    for edge in edges:
        if edge.is_wildcard_source():
            continue

        target_id = f"n_{edge.target_host[:8]}"

        for source_uuid in edge.source_hosts:
            source_id = f"n_{source_uuid[:8]}"

            # Skip duplicates
            edge_key = (source_id, target_id)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            # Check if both hosts are in our list
            source_in_list = any(h.uuid[:8] in source_id for h in hosts)
            target_in_list = any(h.uuid[:8] in target_id for h in hosts)

            if not (source_in_list and target_in_list):
                continue

            # Style based on criticality
            if edge.is_critical:
                style = 'color=red, penwidth=2'
            else:
                style = 'color=black'

            label = f"{edge.target_service}:{edge.target_port}"
            lines.append(f'    {source_id} -> {target_id} [label="{label}", {style}];')

    lines.append("}")

    return "\n".join(lines)
