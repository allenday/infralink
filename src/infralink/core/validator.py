"""Validation helpers for cross-checking registry and edge declarations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from infralink.core.edges import Edge, EdgeSet
from infralink.core.registry import Host, Registry


@dataclass
class ValidationResult:
    """Aggregate of validation errors and warnings."""

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)

    def merge(self, other: "ValidationResult") -> None:
        self.errors.extend(other.errors)
        self.warnings.extend(other.warnings)


def _resolve_selector_hosts(edge: Edge, registry: Registry) -> list[Host]:
    """Resolve hosts from selector-based edge definitions.

    Supports the same selector semantics used by the resolver: role, service,
    and observability.ready. Only active hosts are returned.
    """

    selector = edge.source_selector or {}
    hosts: list[Host] = []

    if "role" in selector:
        hosts.extend(h for h in registry.hosts_with_role(selector["role"]) if h.is_active)

    if "service" in selector:
        hosts.extend(h for h in registry.hosts_with_service(selector["service"]) if h.is_active)

    if "observability.ready" in selector:
        hosts.extend(
            h
            for h in registry.active_hosts()
            if h.to_dict().get("observability", {}).get("ready")
        )

    # Deduplicate while preserving order
    seen: set[str] = set()
    unique_hosts: list[Host] = []
    for host in hosts:
        if host.uuid not in seen:
            unique_hosts.append(host)
            seen.add(host.uuid)

    return unique_hosts


def _resolve_source_hosts(edge: Edge, registry: Registry, result: ValidationResult) -> list[Host]:
    """Resolve all source hosts for an edge, recording missing references."""

    if edge.is_wildcard_source():
        return registry.active_hosts()

    resolved: list[Host] = []
    seen: set[str] = set()

    # Explicit host UUIDs
    for source_uuid in edge.source_hosts:
        host = registry.get_by_uuid(source_uuid)
        if not host:
            result.add_error(f"Edge '{edge.id}': source host not found: {source_uuid}")
            continue
        if host.uuid not in seen:
            resolved.append(host)
            seen.add(host.uuid)

    # Selector-based
    for host in _resolve_selector_hosts(edge, registry):
        if host.uuid not in seen:
            resolved.append(host)
            seen.add(host.uuid)

    return resolved


def validate_edges_against_registry(registry: Registry, edges: EdgeSet) -> ValidationResult:
    """Cross-validate edges against the registry hosts and services.

    Checks include:
    - Target and source hosts exist
    - Target services are declared on target hosts
    - Port conflicts between edge declarations and service configs
    - Orphan host detection (no inbound or outbound edges)
    """

    result = ValidationResult()

    # Track inbound/outbound edges per active host
    inbound_counts = {host.uuid: 0 for host in registry.active_hosts()}
    outbound_counts = {host.uuid: 0 for host in registry.active_hosts()}

    for edge in edges:
        target_host = registry.get_by_uuid(edge.target_host)
        if not target_host:
            result.add_error(f"Edge '{edge.id}': target host not found: {edge.target_host}")
            continue

        if not target_host.is_active:
            result.add_warning(
                f"Edge '{edge.id}': target host is not active: {target_host.canonical_name}"
            )

        if target_host.uuid in inbound_counts:
            inbound_counts[target_host.uuid] += 1

        # Validate target service exists
        if not target_host.has_service(edge.target_service):
            result.add_error(
                f"Edge '{edge.id}': target service '{edge.target_service}' not declared on host "
                f"'{target_host.canonical_name}'"
            )
        else:
            # Check for port mismatch
            service_port = target_host.get_service_port(edge.target_service)
            if service_port is not None and service_port != edge.target_port:
                result.add_error(
                    f"Edge '{edge.id}': port {edge.target_port} does not match service "
                    f"'{edge.target_service}' port {service_port} on host '{target_host.canonical_name}'"
                )

        # Resolve source hosts (explicit, selector, wildcard)
        source_hosts: Iterable[Host] = _resolve_source_hosts(edge, registry, result)
        for host in source_hosts:
            if host.uuid in outbound_counts:
                outbound_counts[host.uuid] += 1

    # Orphan detection (active hosts only)
    for host in registry.active_hosts():
        if inbound_counts.get(host.uuid, 0) == 0 and outbound_counts.get(host.uuid, 0) == 0:
            result.add_warning(
                f"Host '{host.canonical_name}' ({host.uuid_prefix}) has no inbound or outbound edges"
            )

    return result
