"""Markdown documentation generation."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infralink.core.edges import EdgeSet
    from infralink.core.registry import Host, Registry


def generate_host_doc(
    host: Host,
    edges: EdgeSet,
    registry: Registry,
) -> str:
    """Generate Markdown documentation for a single host."""
    lines = [
        f"# {host.canonical_name}",
        "",
        f"**UUID:** `{host.uuid}`  ",
        f"**Status:** {host.status.value}  ",
        f"**Cloud:** {host.cloud or 'unknown'}  ",
        f"**Group:** {host.group or 'unknown'}  ",
        "",
        "## Network",
        "",
        "| Type | Address |",
        "|------|---------|",
        f"| Tailscale | `{host.tailscale_ip or 'N/A'}` |",
        f"| Public IPv4 | `{host.public_ip or 'N/A'}` |",
        "",
    ]

    # Services
    lines.extend([
        "## Services",
        "",
    ])

    if host.services:
        lines.append("| Service | Status |")
        lines.append("|---------|--------|")
        for svc in host.services:
            lines.append(f"| {svc} | Active |")
    else:
        lines.append("*No services declared*")

    lines.append("")

    # Inbound connections
    inbound = edges.targeting_host(host.uuid)
    if inbound:
        lines.extend([
            "## Inbound Connections",
            "",
            "| Source | Service | Port | Protocol | Criticality |",
            "|--------|---------|------|----------|-------------|",
        ])

        for edge in inbound:
            source_hosts = edge.source_hosts
            if edge.is_wildcard_source():
                source_names = ["*"]
            else:
                source_names = []
                for src_uuid in source_hosts[:3]:  # Limit display
                    src_host = registry.get_by_uuid(src_uuid)
                    if src_host:
                        source_names.append(src_host.canonical_name)
                    else:
                        source_names.append(src_uuid[:8])

            sources_str = ", ".join(source_names)
            if len(source_hosts) > 3:
                sources_str += f" (+{len(source_hosts) - 3} more)"

            lines.append(
                f"| {sources_str} | {edge.target_service} | {edge.target_port} | "
                f"{edge.protocol or '-'} | {edge.criticality.value} |"
            )

        lines.append("")

    # Outbound connections
    outbound = edges.from_host(host.uuid)
    if outbound:
        lines.extend([
            "## Outbound Connections",
            "",
            "| Target | Service | Port | Purpose |",
            "|--------|---------|------|---------|",
        ])

        for edge in outbound:
            target_host = registry.get_by_uuid(edge.target_host)
            target_name = target_host.canonical_name if target_host else edge.target_host[:8]
            purpose = edge.purpose or "-"
            lines.append(
                f"| {target_name} | {edge.target_service} | {edge.target_port} | {purpose} |"
            )

        lines.append("")

    # Footer
    lines.extend([
        "---",
        f"*Generated: {datetime.now().isoformat()}*",
    ])

    return "\n".join(lines)


def generate_index(registry: Registry, edges: EdgeSet) -> str:
    """Generate index page listing all hosts."""
    lines = [
        "# Infrastructure Host Index",
        "",
        f"Total hosts: {len(registry)} ({len(registry.active_hosts())} active)  ",
        f"Total edges: {len(edges)}  ",
        "",
        "## Active Hosts",
        "",
        "| Host | UUID | Group | Cloud | Services |",
        "|------|------|-------|-------|----------|",
    ]

    for host in sorted(registry.active_hosts(), key=lambda h: h.canonical_name):
        services = ", ".join(host.services[:3])
        if len(host.services) > 3:
            services += "..."

        lines.append(
            f"| [{host.canonical_name}]({host.canonical_name}.md) | "
            f"`{host.uuid_prefix}...` | {host.group or '-'} | "
            f"{host.cloud or '-'} | {services} |"
        )

    # Groups summary
    lines.extend([
        "",
        "## By Group",
        "",
    ])

    for group in sorted(registry.groups()):
        hosts_in_group = registry.filter(group=group)
        active_count = len([h for h in hosts_in_group if h.is_active])
        lines.append(f"- **{group}**: {active_count} active hosts")

    # Clouds summary
    lines.extend([
        "",
        "## By Cloud Provider",
        "",
    ])

    for cloud in sorted(registry.clouds()):
        hosts_in_cloud = registry.filter(cloud=cloud)
        active_count = len([h for h in hosts_in_cloud if h.is_active])
        lines.append(f"- **{cloud}**: {active_count} active hosts")

    lines.extend([
        "",
        "---",
        f"*Generated: {datetime.now().isoformat()}*",
    ])

    return "\n".join(lines)


def generate_edge_index(edges: EdgeSet, registry: Registry) -> str:
    """Generate index page listing all edges."""
    lines = [
        "# Infrastructure Edge Index",
        "",
        f"Total edges: {len(edges)}  ",
        f"Critical edges: {len(edges.critical_edges())}  ",
        "",
        "## All Edges",
        "",
        "| ID | Type | Target | Port | Criticality | Sources |",
        "|----|------|--------|------|-------------|---------|",
    ]

    for edge in sorted(edges, key=lambda e: e.id):
        target_host = registry.get_by_uuid(edge.target_host)
        target_name = target_host.canonical_name if target_host else edge.target_host[:8]

        if edge.is_wildcard_source():
            sources = "*"
        else:
            sources = str(len(edge.source_hosts))

        lines.append(
            f"| {edge.id} | {edge.type.value} | {target_name}/{edge.target_service} | "
            f"{edge.target_port} | {edge.criticality.value} | {sources} |"
        )

    # By type
    lines.extend([
        "",
        "## By Type",
        "",
    ])

    from infralink.core.schema import EdgeType

    for etype in EdgeType:
        type_edges = edges.by_type(etype)
        if type_edges:
            lines.append(f"- **{etype.value}**: {len(type_edges)} edges")

    lines.extend([
        "",
        "---",
        f"*Generated: {datetime.now().isoformat()}*",
    ])

    return "\n".join(lines)
