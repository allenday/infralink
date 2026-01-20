"""Mermaid diagram generation."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infralink.core.edges import EdgeSet
    from infralink.core.registry import Host, Registry


def _sanitize_id(name: str) -> str:
    """Sanitize a string for use as a Mermaid node ID."""
    return name.replace("-", "_").replace(".", "_").replace(":", "_")


def _get_all_services(host: Host) -> list[str]:
    """Get all services for a host (roles + one-off services)."""
    services = list(host.roles)
    services.extend(host.service_names)
    return services


def generate_mermaid(
    hosts: list[Host],
    edges: EdgeSet,
    registry: Registry,
) -> str:
    """
    Generate Mermaid flowchart diagram with host subgraphs colored by group.

    Structure:
    - Host subgraphs contain service nodes (roles + one-off services)
    - Hosts are colored by their group
    - Edges connect service nodes across hosts

    Returns Markdown with embedded Mermaid diagram.
    """
    lines = ["# Infrastructure Topology", "", "```mermaid", "flowchart LR"]

    # Group hosts by group for color assignment
    groups: dict[str, list[Host]] = defaultdict(list)
    for host in hosts:
        group = host.group or "other"
        groups[group].append(host)

    # Color palette for groups
    group_colors = [
        "#e1f5fe",  # light blue
        "#f3e5f5",  # light purple
        "#e8f5e9",  # light green
        "#fff3e0",  # light orange
        "#fce4ec",  # light pink
        "#e0f2f1",  # light teal
        "#f5f5f5",  # light gray
        "#fffde7",  # light yellow
    ]

    # Assign colors to groups
    group_color_map: dict[str, str] = {}
    for i, group in enumerate(sorted(groups.keys())):
        group_color_map[group] = group_colors[i % len(group_colors)]

    # Build a lookup of host UUID prefix -> Host for edge resolution
    host_lookup: dict[str, Host] = {h.uuid[:8]: h for h in hosts}

    # Define host subgraphs with services (flat structure, no group nesting)
    for host in sorted(hosts, key=lambda h: (h.group or "", h.canonical_name)):
        host_id = host.uuid[:8]
        host_label = host.canonical_name

        lines.append(f"    subgraph {host_id}[{host_label}]")

        # Add role nodes (standard services from roles) - stadium shape
        for role in host.roles:
            node_id = f"{host_id}_{_sanitize_id(role)}"
            lines.append(f"        {node_id}([{role}])")

        # Add one-off service nodes - rectangle shape
        for svc_name in host.service_names:
            node_id = f"{host_id}_{_sanitize_id(svc_name)}"
            lines.append(f"        {node_id}[{svc_name}]")

        lines.append("    end")

    # Add edges connecting specific services
    lines.append("")
    lines.append("    %% Connections")

    seen_edges: set[tuple[str, str, str]] = set()
    for edge in edges:
        if edge.is_wildcard_source():
            continue

        target_prefix = edge.target_host[:8]
        target_service = edge.target_service

        # Check if target host is in our list
        if target_prefix not in host_lookup:
            continue

        for source_uuid in edge.source_hosts:
            source_prefix = source_uuid[:8]

            # Check if source host is in our list
            if source_prefix not in host_lookup:
                continue

            # Get source service from edge
            source_service = edge.source_service or "unknown"

            # Create unique edge key
            edge_key = (source_prefix, source_service, target_prefix, target_service)
            if edge_key in seen_edges:
                continue
            seen_edges.add(edge_key)

            # Build node IDs
            source_node = f"{source_prefix}_{_sanitize_id(source_service)}"
            target_node = f"{target_prefix}_{_sanitize_id(target_service)}"

            # Style based on criticality
            arrow = "==>" if edge.is_critical else "-->"

            lines.append(f"    {source_node} {arrow} {target_node}")

    # Add style definitions for group colors
    lines.append("")
    lines.append("    %% Group colors")
    for group, color in sorted(group_color_map.items()):
        class_name = _sanitize_id(group)
        lines.append(f"    classDef {class_name} fill:{color},stroke:#333,stroke-width:1px")

    # Apply styles to host subgraphs
    lines.append("")
    lines.append("    %% Apply group colors to hosts")
    for host in hosts:
        host_id = host.uuid[:8]
        group = host.group or "other"
        class_name = _sanitize_id(group)
        lines.append(f"    class {host_id} {class_name}")

    lines.append("```")
    lines.append("")
    lines.append("## Legend")
    lines.append("")

    # Show group colors in legend
    lines.append("### Groups")
    lines.append("")
    for group, color in sorted(group_color_map.items()):
        lines.append(f"- **{group}**: `{color}`")

    lines.append("")
    lines.append("### Shapes")
    lines.append("")
    lines.append("- `(role)` Standardized role (from roles.yml) - stadium shape")
    lines.append("- `[service]` One-off service - rectangle shape")
    lines.append("- `==>` Critical connection")
    lines.append("- `-->` Standard connection")

    return "\n".join(lines)
