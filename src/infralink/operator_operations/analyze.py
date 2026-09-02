"""Analyze a registry and generate deterministic derived artifacts."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import date, datetime
from enum import Enum
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import Field, StrictInt

from infralink.cli.artifacts import (
    artifact_fingerprint,
    artifact_metadata,
    artifact_pages,
    require_output,
    write_artifacts,
)
from infralink.cli.contracts import AnalysisSummary, AnalyzeResult, Artifact, Diagnostic, Page
from infralink.cli.pagination import invalid_cursor
from infralink.operator_sources import SourceRequest, load_registry


class AnalyzeRequest(SourceRequest):
    """Generate deterministic public artifacts from one Registry checkout."""

    output: Path
    include_edges: bool = True
    include_diagram: bool = True
    include_monitoring: bool = True
    limit: StrictInt = Field(default=20, ge=1, le=1000)
    cursor: str | None = None
    collection: str | None = None


def _host_id(name: str, host_data: dict[str, Any]) -> str | None:
    value = host_data.get("uuid", name)
    return value if isinstance(value, str) and value else None


_PUBLIC_HOST_FIELDS = (
    "canonical_name",
    "status",
    "group",
    "cloud",
    "tailscale_ip",
    "tailscale_name",
    "public_ip",
    "private_ip",
)
_PUBLIC_SERVICE_FIELDS = ("port", "protocol", "exposure")


def _public_host(name: str, raw_host: dict[str, Any]) -> dict[str, Any]:
    host = {
        field: raw_host[field]
        for field in _PUBLIC_HOST_FIELDS
        if isinstance(raw_host.get(field), (str, int, bool))
    }
    host.setdefault("canonical_name", name)

    roles = raw_host.get("roles")
    if isinstance(roles, list):
        host["roles"] = [role for role in roles if isinstance(role, str)]

    services = raw_host.get("services")
    if isinstance(services, list):
        host["services"] = [service for service in services if isinstance(service, str)]
    elif isinstance(services, dict):
        public_services: dict[str, Any] = {}
        for service_name, service in sorted(services.items()):
            if not isinstance(service_name, str):
                continue
            if isinstance(service, dict):
                public_services[service_name] = {
                    field: service[field]
                    for field in _PUBLIC_SERVICE_FIELDS
                    if isinstance(service.get(field), (str, int, bool))
                }
        host["services"] = public_services
    return host


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
        new_hosts[identity] = _public_host(str(name), raw_host)
    return {"hosts": new_hosts}


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
                        "metadata": {"source": "service_dependencies"},
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


def _analysis_data(registry: Any) -> dict[str, Any]:
    """Render parsed checkout hosts without changing artifact semantics."""
    hosts: dict[str, dict[str, Any]] = {}
    for host in registry:
        data = host.to_dict()
        if host.group is not None:
            data["group"] = host.group
        hosts[host.uuid] = data
    return cast(dict[str, Any], json.loads(json.dumps({"hosts": hosts}, default=_json_scalar)))


def _json_scalar(value: object) -> object:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported analysis value: {type(value).__name__}")


def analyze_declared_registry(request: AnalyzeRequest) -> AnalyzeResult:
    """Generate local artifacts from the selected checkout without deployment effects."""
    output = require_output(request.output)
    loaded = load_registry(request)
    try:
        data = _analysis_data(loaded.registry)
    except Exception as error:
        from agent_surface import OperationError

        raise OperationError(
            "source_invalid",
            "Registry source could not be analyzed",
            details=({"source": "registry", "path": str(loaded.registry_source_path)},),
            fix="Correct the registry declaration and validate it before retrying.",
        ) from error

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
    edge_list = infer_edges_from_dependencies(data) if request.include_edges else []
    if request.include_edges and request.include_monitoring:
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
    if request.include_edges:
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
    if request.include_diagram:
        generated.append(
            (
                Path("diagram.mmd"),
                "text/vnd.mermaid",
                generate_mermaid_diagram(data, edge_list).encode("utf-8"),
            )
        )
    artifacts = artifact_metadata(output, generated)
    collections: dict[str, Sequence[object]] = {
        "diagnostics": diagnostics,
        "artifacts": artifacts,
    }
    selected = request.collection or "diagnostics"
    if selected not in collections:
        raise invalid_cursor()
    fingerprint = artifact_fingerprint(
        command="analyze",
        sources=[loaded.registry_path],
        options={
            "output": output.as_posix(),
            "include_edges": request.include_edges,
            "include_diagram": request.include_diagram,
            "include_monitoring": request.include_monitoring,
        },
        collections=collections,
    )
    diagnostic_pages = artifact_pages(
        command="analyze",
        collections={"diagnostics": diagnostics},
        selected="diagnostics",
        cursor=request.cursor if selected == "diagnostics" else None,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    artifact_result_pages = artifact_pages(
        command="analyze",
        collections={"artifacts": artifacts},
        selected="artifacts",
        cursor=request.cursor if selected == "artifacts" else None,
        limit=request.limit,
        fingerprint=fingerprint,
    )
    diagnostic_page: Page[Diagnostic] = diagnostic_pages["diagnostics"]
    artifact_page: Page[Artifact] = artifact_result_pages["artifacts"]
    write_artifacts(output, generated)
    return AnalyzeResult(
        analysis=AnalysisSummary(
            host_count=len(data.get("hosts", {})),
            service_count=_service_count(data),
            edge_count=len(edge_list),
            diagnostics=diagnostic_page,
        ),
        artifacts=artifact_page,
    )
