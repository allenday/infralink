"""Analyze production registry and generate edges/diagrams."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table

console = Console()


def convert_to_uuid_primary(data: dict[str, Any]) -> dict[str, Any]:
    """Convert old format (name key + uuid field) to UUID-as-primary format."""
    new_hosts = {}
    for name, host_data in data.get("hosts", {}).items():
        uuid = host_data.get("uuid")
        if not uuid:
            console.print(f"[yellow]Warning: Host {name} has no UUID, skipping[/yellow]")
            continue
        # Remove uuid from data since it's now the key
        host_copy = {k: v for k, v in host_data.items() if k != "uuid"}
        # Ensure canonical_name exists
        if "canonical_name" not in host_copy:
            host_copy["canonical_name"] = name
        new_hosts[uuid] = host_copy

    return {
        "hosts": new_hosts,
        "ansible_defaults": data.get("ansible_defaults", {}),
    }


def infer_edges_from_dependencies(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract edges from service_dependencies declarations."""
    edges = []

    for name, host_data in data.get("hosts", {}).items():
        source_uuid = host_data.get("uuid")
        deps = host_data.get("service_dependencies", {})

        for service, dep_list in deps.items():
            for dep in dep_list:
                target_host = dep.get("host")
                target_service = dep.get("service")
                target_port = dep.get("port")

                if not target_host or not target_service:
                    continue

                # Skip cloud SQL references for now
                if target_host.startswith("cloudsql:"):
                    continue

                edge_id = f"{host_data.get('canonical_name', name)}-{service}-to-{target_service}"
                edges.append({
                    "id": edge_id,
                    "type": "database" if target_service in ("mariadb", "mysql", "postgresql", "postgres") else "queue",
                    "from": {
                        "hosts": [source_uuid],
                        "service": service,
                    },
                    "to": {
                        "host": target_host,
                        "service": target_service,
                        "port": target_port or 3306,
                    },
                    "metadata": {
                        "source": "service_dependencies",
                        "notes": dep.get("notes"),
                    },
                })

    return edges


def infer_monitoring_edges(data: dict[str, Any], prometheus_uuid: str | None) -> list[dict[str, Any]]:
    """Infer prometheus scrape edges from observability declarations."""
    if not prometheus_uuid:
        return []

    edges = []

    for name, host_data in data.get("hosts", {}).items():
        if host_data.get("status") != "active":
            continue

        target_uuid = host_data.get("uuid")
        obs = host_data.get("observability", {})
        managed = obs.get("managed_services", [])

        # Standard exporter ports
        exporter_ports = {
            "node-exporter": 9100,
            "cadvisor": 8080,
            "mysqld-exporter": 9104,
            "postgres-exporter": 9187,
            "redis-exporter": 9121,
            "nginx-exporter": 9113,
            "nginx-vts-exporter": 9913,
            "php-fpm-exporter": 9253,
            "elasticsearch-exporter": 9114,
            "airflow-exporter": 9112,
            "postfix-exporter": 9154,
            "dcgm-exporter": 9400,
        }

        # Check for port overrides
        overrides = obs.get("port_overrides", {})

        for service in managed:
            if service in exporter_ports or service.endswith("-exporter"):
                port = overrides.get(service) or exporter_ports.get(service, 9100)
                edge_id = f"prometheus-to-{host_data.get('canonical_name', name)}-{service}"
                edges.append({
                    "id": edge_id,
                    "type": "monitoring",
                    "from": {
                        "hosts": [prometheus_uuid],
                        "service": "prometheus",
                    },
                    "to": {
                        "host": target_uuid,
                        "service": service,
                        "port": port,
                    },
                    "metadata": {
                        "source": "observability.managed_services",
                    },
                })

    return edges


def generate_mermaid_diagram(data: dict[str, Any], edges: list[dict[str, Any]]) -> str:
    """Generate Mermaid diagram from registry and edges."""
    lines = ["graph LR"]

    # Group hosts by group
    groups: dict[str, list[tuple[str, dict]]] = {}
    for name, host_data in data.get("hosts", {}).items():
        if host_data.get("status") != "active":
            continue
        group = host_data.get("group", "ungrouped")
        if group not in groups:
            groups[group] = []
        groups[group].append((name, host_data))

    # Add subgraphs for each group
    for group, hosts in sorted(groups.items()):
        lines.append(f"    subgraph {group}")
        for name, host_data in hosts:
            uuid_short = host_data.get("uuid", "")[:8]
            canonical = host_data.get("canonical_name", name)
            services = host_data.get("services", [])[:3]  # Top 3 services
            svc_str = ", ".join(services) if services else "no services"
            lines.append(f'        {uuid_short}["{canonical}<br/>{svc_str}"]')
        lines.append("    end")

    # Add edges
    for edge in edges:
        from_hosts = edge.get("from", {}).get("hosts", [])
        to_host = edge.get("to", {}).get("host", "")
        to_service = edge.get("to", {}).get("service", "")
        edge_type = edge.get("type", "")

        for from_host in from_hosts:
            from_short = from_host[:8]
            to_short = to_host[:8]
            lines.append(f"    {from_short} -->|{edge_type}:{to_service}| {to_short}")

    return "\n".join(lines)


@click.command()
@click.option(
    "-r", "--registry",
    type=click.Path(exists=True, path_type=Path),
    default="../../ansible/inventory/uuid_registry.yml",
    help="Path to production registry",
)
@click.option(
    "-o", "--output",
    type=click.Path(path_type=Path),
    default="local",
    help="Output directory",
)
@click.option("--edges/--no-edges", default=True, help="Generate edges.yml")
@click.option("--diagram/--no-diagram", default=True, help="Generate diagram")
@click.option("--monitoring/--no-monitoring", default=True, help="Include monitoring edges")
def analyze(
    registry: Path,
    output: Path,
    edges: bool,
    diagram: bool,
    monitoring: bool,
) -> None:
    """Analyze production registry and generate local analysis files.

    Reads the production uuid_registry.yml (read-only) and generates:
    - local/registry.yml - Converted to UUID-as-primary format
    - local/edges.yml - Inferred edges from service_dependencies
    - local/diagram.mmd - Mermaid diagram
    """
    # Load prod registry
    console.print(f"[cyan]Loading registry from {registry}[/cyan]")
    with registry.open() as f:
        data = yaml.safe_load(f)

    # Stats
    total_hosts = len(data.get("hosts", {}))
    active_hosts = sum(1 for h in data.get("hosts", {}).values() if h.get("status") == "active")
    console.print(f"  Total hosts: {total_hosts}")
    console.print(f"  Active hosts: {active_hosts}")

    # Find prometheus host
    prometheus_uuid = None
    for name, host_data in data.get("hosts", {}).items():
        if "prometheus" in host_data.get("services", []):
            prometheus_uuid = host_data.get("uuid")
            console.print(f"  Prometheus host: {host_data.get('canonical_name')} ({prometheus_uuid[:8]}...)")
            break

    # Create output directory
    output.mkdir(parents=True, exist_ok=True)

    # Convert to UUID-primary format
    converted = convert_to_uuid_primary(data)
    converted_path = output / "registry.yml"
    with converted_path.open("w") as f:
        yaml.dump(converted, f, default_flow_style=False, sort_keys=False)
    console.print(f"[green]Wrote {converted_path}[/green]")

    # Generate edges
    if edges:
        edge_list = infer_edges_from_dependencies(data)
        console.print(f"  Inferred {len(edge_list)} edges from service_dependencies")

        if monitoring and prometheus_uuid:
            mon_edges = infer_monitoring_edges(data, prometheus_uuid)
            console.print(f"  Inferred {len(mon_edges)} monitoring edges")
            edge_list.extend(mon_edges)

        edges_data = {
            "schema_version": "1.0",
            "edges": edge_list,
        }
        edges_path = output / "edges.yml"
        with edges_path.open("w") as f:
            yaml.dump(edges_data, f, default_flow_style=False, sort_keys=False)
        console.print(f"[green]Wrote {edges_path}[/green]")

        # Show edge summary
        if edge_list:
            table = Table(title="Inferred Edges")
            table.add_column("ID")
            table.add_column("Type")
            table.add_column("Target")
            table.add_column("Source")

            for e in edge_list[:20]:  # Show first 20
                table.add_row(
                    e["id"][:40],
                    e["type"],
                    f"{e['to']['service']}:{e['to']['port']}",
                    e.get("metadata", {}).get("source", ""),
                )
            console.print(table)
            if len(edge_list) > 20:
                console.print(f"  ... and {len(edge_list) - 20} more")

    # Generate diagram
    if diagram:
        mermaid = generate_mermaid_diagram(data, edge_list if edges else [])
        diagram_path = output / "diagram.mmd"
        with diagram_path.open("w") as f:
            f.write(mermaid)
        console.print(f"[green]Wrote {diagram_path}[/green]")

    # Summary of gaps
    console.print("\n[bold]Gap Analysis:[/bold]")

    # Hosts without observability.ready
    not_ready = [
        h.get("canonical_name", n)
        for n, h in data.get("hosts", {}).items()
        if h.get("status") == "active" and not h.get("observability", {}).get("ready")
    ]
    if not_ready:
        console.print(f"  [yellow]Hosts not observability-ready ({len(not_ready)}):[/yellow]")
        for name in not_ready[:10]:
            console.print(f"    - {name}")
        if len(not_ready) > 10:
            console.print(f"    ... and {len(not_ready) - 10} more")

    # Hosts with unmanaged services
    unmanaged = []
    for n, h in data.get("hosts", {}).items():
        if h.get("status") != "active":
            continue
        um = h.get("observability", {}).get("unmanaged_services", [])
        if um:
            unmanaged.append((h.get("canonical_name", n), um))

    if unmanaged:
        console.print(f"  [yellow]Hosts with unmanaged services ({len(unmanaged)}):[/yellow]")
        for name, services in unmanaged[:10]:
            console.print(f"    - {name}: {', '.join(services)}")

    # Missing exporters
    missing_exp = []
    for n, h in data.get("hosts", {}).items():
        if h.get("status") != "active":
            continue
        me = h.get("observability", {}).get("missing_exporters", [])
        if me:
            missing_exp.append((h.get("canonical_name", n), me))

    if missing_exp:
        console.print(f"  [yellow]Hosts with missing exporters ({len(missing_exp)}):[/yellow]")
        for name, exporters in missing_exp:
            console.print(f"    - {name}: {', '.join(exporters)}")
