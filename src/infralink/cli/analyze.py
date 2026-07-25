"""Analyze a registry and generate deterministic derived artifacts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click
import yaml

from infralink.cli.actions import action
from infralink.cli.artifacts import (
    artifact_fingerprint,
    artifact_pages,
    continuation_actions,
    require_output,
    write_artifacts,
)
from infralink.cli.contracts import AnalyzeResult, Diagnostic
from infralink.cli.main import (
    Context,
    _context_for,
    _emit,
    _page_options,
    _root_source_argv,
    input_load_failed,
    pass_context,
)
from infralink.cli.output import ok_envelope


def _host_id(name: str, host_data: dict[str, Any]) -> str | None:
    value = host_data.get("uuid", name)
    return value if isinstance(value, str) and value else None


def convert_to_uuid_primary(
    data: dict[str, Any],
    diagnostics: list[Diagnostic],
) -> dict[str, Any]:
    """Convert both name-keyed legacy and UUID-keyed registries."""
    new_hosts: dict[str, Any] = {}
    for name, raw_host in sorted(data.get("hosts", {}).items()):
        if not isinstance(raw_host, dict):
            diagnostics.append(
                Diagnostic(
                    code="invalid_host",
                    path=f"hosts.{name}",
                    message="Host declaration is not an object",
                    severity="warning",
                )
            )
            continue
        identity = _host_id(str(name), raw_host)
        if identity is None:
            diagnostics.append(
                Diagnostic(
                    code="missing_host_id",
                    path=f"hosts.{name}",
                    message="Host has no usable identity",
                    severity="warning",
                )
            )
            continue
        host_copy = {key: value for key, value in raw_host.items() if key != "uuid"}
        host_copy.setdefault("canonical_name", str(name))
        new_hosts[identity] = host_copy
    return {
        "hosts": new_hosts,
        "ansible_defaults": data.get("ansible_defaults", {}),
    }


def infer_edges_from_dependencies(data: dict[str, Any]) -> list[dict[str, Any]]:
    edges: list[dict[str, Any]] = []
    for name, host_data in sorted(data.get("hosts", {}).items()):
        if not isinstance(host_data, dict):
            continue
        source_id = _host_id(str(name), host_data)
        dependencies = host_data.get("service_dependencies", {})
        if not isinstance(dependencies, dict):
            continue
        for service, dependency_list in sorted(dependencies.items()):
            if not isinstance(dependency_list, list):
                continue
            for dependency in dependency_list:
                if not isinstance(dependency, dict):
                    continue
                target_host = dependency.get("host")
                target_service = dependency.get("service")
                if (
                    source_id is None
                    or not isinstance(target_host, str)
                    or not isinstance(target_service, str)
                    or target_host.startswith("cloudsql:")
                ):
                    continue
                edges.append(
                    {
                        "id": (
                            f"{host_data.get('canonical_name', name)}-{service}-to-{target_service}"
                        ),
                        "type": (
                            "database"
                            if target_service in ("mariadb", "mysql", "postgresql", "postgres")
                            else "queue"
                        ),
                        "from": {"hosts": [source_id], "service": service},
                        "to": {
                            "host": target_host,
                            "service": target_service,
                            "port": dependency.get("port") or 3306,
                        },
                        "metadata": {
                            "source": "service_dependencies",
                            "notes": dependency.get("notes"),
                        },
                    }
                )
    return sorted(edges, key=lambda edge: str(edge["id"]))


def infer_monitoring_edges(
    data: dict[str, Any],
    prometheus_id: str | None,
) -> list[dict[str, Any]]:
    if prometheus_id is None:
        return []
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
    inferred: list[dict[str, Any]] = []
    for name, host_data in sorted(data.get("hosts", {}).items()):
        if not isinstance(host_data, dict) or host_data.get("status") != "active":
            continue
        target_id = _host_id(str(name), host_data)
        observability = host_data.get("observability", {})
        if target_id is None or not isinstance(observability, dict):
            continue
        overrides = observability.get("port_overrides", {})
        overrides = overrides if isinstance(overrides, dict) else {}
        for service in sorted(observability.get("managed_services", [])):
            if service not in exporter_ports and not service.endswith("-exporter"):
                continue
            inferred.append(
                {
                    "id": (f"prometheus-to-{host_data.get('canonical_name', name)}-{service}"),
                    "type": "monitoring",
                    "from": {"hosts": [prometheus_id], "service": "prometheus"},
                    "to": {
                        "host": target_id,
                        "service": service,
                        "port": overrides.get(service) or exporter_ports.get(service, 9100),
                    },
                    "metadata": {"source": "observability.managed_services"},
                }
            )
    return sorted(inferred, key=lambda edge: str(edge["id"]))


def generate_mermaid_diagram(data: dict[str, Any], edges: list[dict[str, Any]]) -> str:
    lines = ["graph LR"]
    groups: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for name, host_data in sorted(data.get("hosts", {}).items()):
        if not isinstance(host_data, dict) or host_data.get("status") != "active":
            continue
        groups.setdefault(str(host_data.get("group", "ungrouped")), []).append(
            (str(name), host_data)
        )
    for group, hosts in sorted(groups.items()):
        lines.append(f"    subgraph {group}")
        for name, host_data in sorted(hosts):
            identity = _host_id(name, host_data) or ""
            canonical = host_data.get("canonical_name", name)
            services = host_data.get("services", [])
            service_names = sorted(services)[:3] if isinstance(services, (dict, list)) else []
            label = ", ".join(service_names) if service_names else "no services"
            lines.append(f'        {identity[:8]}["{canonical}<br/>{label}"]')
        lines.append("    end")
    for edge in sorted(edges, key=lambda item: str(item.get("id", ""))):
        for source in sorted(edge.get("from", {}).get("hosts", [])):
            target = str(edge.get("to", {}).get("host", ""))
            service = edge.get("to", {}).get("service", "")
            lines.append(f"    {source[:8]} -->|{edge.get('type', '')}:{service}| {target[:8]}")
    return "\n".join(lines)


def _service_count(data: dict[str, Any]) -> int:
    services: set[str] = set()
    for host in data.get("hosts", {}).values():
        if not isinstance(host, dict):
            continue
        for field in ("roles", "services"):
            values = host.get(field, [])
            if isinstance(values, dict):
                services.update(str(value) for value in values)
            elif isinstance(values, list):
                services.update(str(value) for value in values)
    return len(services)


@click.command()
@click.option(
    "-r",
    "--registry",
    "registry_override",
    type=click.Path(exists=False, path_type=Path),
    default=None,
)
@click.option("-o", "--output", type=click.Path(path_type=Path), required=True)
@click.option("--edges/--no-edges", default=True)
@click.option("--diagram/--no-diagram", default=True)
@click.option("--monitoring/--no-monitoring", default=True)
@_page_options
@pass_context
def analyze(
    ctx: Context,
    registry_override: Path | None,
    output: Path | None,
    edges: bool,
    diagram: bool,
    monitoring: bool,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Analyze a registry and generate local derived artifacts."""
    output = require_output(output)
    source = registry_override or ctx.registry_path
    if source is None or not source.exists() or source.is_dir():
        raise input_load_failed("registry", str(source))
    try:
        loaded = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(loaded, dict) or not isinstance(loaded.get("hosts", {}), dict):
            raise ValueError("invalid registry root")
        data: dict[str, Any] = loaded
    except Exception:
        raise input_load_failed("registry", str(source)) from None

    diagnostics: list[Diagnostic] = []
    converted = convert_to_uuid_primary(data, diagnostics)
    prometheus_id = next(
        (
            _host_id(str(name), host)
            for name, host in sorted(data.get("hosts", {}).items())
            if isinstance(host, dict) and "prometheus" in host.get("services", [])
        ),
        None,
    )
    edge_list = infer_edges_from_dependencies(data) if edges else []
    if edges and monitoring:
        edge_list = sorted(
            [*edge_list, *infer_monitoring_edges(data, prometheus_id)],
            key=lambda edge: str(edge["id"]),
        )

    generated: list[tuple[Path, str, bytes]] = [
        (
            Path("registry.yml"),
            "application/yaml",
            yaml.safe_dump(converted, sort_keys=True).encode("utf-8"),
        )
    ]
    if edges:
        generated.append(
            (
                Path("edges.yml"),
                "application/yaml",
                yaml.safe_dump(
                    {"schema_version": "1.0", "edges": edge_list},
                    sort_keys=True,
                ).encode("utf-8"),
            )
        )
    if diagram:
        generated.append(
            (
                Path("diagram.mmd"),
                "text/vnd.mermaid",
                generate_mermaid_diagram(data, edge_list).encode("utf-8"),
            )
        )
    artifacts = write_artifacts(output, generated)
    collections: dict[str, list[object]] = {
        "diagnostics": diagnostics,
        "artifacts": artifacts,
    }
    selected = collection or "diagnostics"
    fingerprint = artifact_fingerprint(
        command="analyze",
        sources=[source],
        options={
            "output": output.as_posix(),
            "edges": edges,
            "diagram": diagram,
            "monitoring": monitoring,
        },
        collections=collections,
    )
    pages = artifact_pages(
        command="analyze",
        collections=collections,
        selected=selected,
        cursor=cursor,
        limit=limit,
        fingerprint=fingerprint,
    )
    result = AnalyzeResult(
        analysis={
            "host_count": len(data.get("hosts", {})),
            "service_count": _service_count(data),
            "edge_count": len(edge_list),
            "diagnostics": pages["diagnostics"],
        },
        artifacts=pages["artifacts"],
    )
    base_argv = [*_root_source_argv(ctx), "analyze", "--output", output.as_posix()]
    if registry_override is not None:
        base_argv.extend(["--registry", str(registry_override)])
    if not edges:
        base_argv.append("--no-edges")
    if not diagram:
        base_argv.append("--no-diagram")
    if not monitoring:
        base_argv.append("--no-monitoring")
    actions = [
        action("help", ["infralink", "help", "analyze"], "Show analyze help"),
        *continuation_actions(
            base_argv=base_argv,
            limit=limit,
            pages=pages,
            sources={
                "diagnostics": "result.analysis.diagnostics.page.next_cursor",
                "artifacts": "result.artifacts.page.next_cursor",
            },
        ),
    ]
    payload = ok_envelope(_context_for(path=["analyze"]), result, actions)
    payload["meta"]["truncated"] = any(page.page.next_cursor is not None for page in pages.values())
    _emit(payload)
