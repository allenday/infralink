"""Pure validation for one declared Infra Registry checkout."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Literal

import yaml
from agent_surface import OperationError
from pydantic import BaseModel, ConfigDict, Field

from infralink.cli.contracts import (
    FleetValidationDiagnostic,
    FleetValidationResult,
    FleetValidationSummary,
)
from infralink.core.registry import Host
from infralink.operator_sources import LoadedSources

_DB_PROTOCOLS = {"postgres", "postgresql", "mysql", "mariadb"}
_DB_GLOBAL_USERS = {"admin": "root", "ops": "ops"}
_DB_SCOPED_ROLES = {"rw", "ro"}
_DB_ROLES = {*_DB_GLOBAL_USERS, *_DB_SCOPED_ROLES}
_LITERAL_INCLUDE_PATTERN = re.compile(r"{%-?\s*include\s+['\"]([^'\"]+)['\"]\s*-?%}")
_INCLUDE_PATTERN = re.compile(r"{%-?\s*include\b[^%]*?-?%}")
_MAX_LITERAL_INCLUDE_DEPTH = 16

__all__ = ["FleetValidationResult", "validate_fleet"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _ComposeIncludeError(ValueError):
    """A Compose include cannot be safely resolved by static validation."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class RoleDefinition(_Model):
    model_config = ConfigDict(extra="ignore", frozen=True)

    requires_params: tuple[str, ...] = ()
    requires_roles: tuple[str, ...] = ()
    compose_service: str | None = None


class ServiceDefinition(_Model):
    model_config = ConfigDict(extra="ignore", frozen=True)

    compose_service: str | None = None


class RoleServiceCatalog(_Model):
    roles: dict[str, RoleDefinition] = Field(default_factory=dict)
    services: dict[str, ServiceDefinition] = Field(default_factory=dict)


def load_role_service_catalog(registry_root: Path) -> RoleServiceCatalog:
    """Load the one ancillary catalog governed by the selected checkout."""
    path = registry_root / "ansible" / "services.yml"
    if not path.is_file():
        raise OperationError(
            "source_not_found",
            "Role and service catalog does not exist",
            details=({"source": "role_service_catalog", "path": str(path)},),
            fix="Add ansible/services.yml to the selected registry checkout.",
        )
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("catalog must be a mapping")
        if not isinstance(data.get("roles"), dict) or not isinstance(data.get("services"), dict):
            raise ValueError("catalog requires mapping roles and services sections")
        return RoleServiceCatalog.model_validate(data)
    except Exception as error:
        raise OperationError(
            "source_invalid",
            "Role and service catalog could not be loaded",
            details=({"source": "role_service_catalog", "path": str(path)},),
            fix="Correct ansible/services.yml in the selected registry checkout.",
        ) from error


def validate_fleet(
    sources: LoadedSources,
    *,
    host: str | None = None,
    strict: bool = False,
    live: bool = False,
    now: datetime | None = None,
) -> FleetValidationResult:
    """Validate static registry semantics without any host-side operation."""
    catalog = load_role_service_catalog(sources.registry_path)
    active_hosts = sorted(sources.registry.active_hosts(), key=lambda item: item.canonical_name)
    if host is not None:
        active_hosts = [item for item in active_hosts if item.canonical_name == host]
        if not active_hosts:
            raise OperationError(
                "host_not_found",
                "Active host was not found in the registry",
                details=({"host": host},),
                fix="Pass an active host canonical_name from infralink host list.",
            )

    diagnostics: list[FleetValidationDiagnostic] = []
    _validate_unique_hosts(active_hosts, diagnostics)
    for item in active_hosts:
        _validate_host(item, catalog, diagnostics, sources.registry_path)
    _validate_database_edges(sources, diagnostics)
    live_evidence = None
    if live:
        from infralink.fleet.live_evidence import evaluate_live_evidence

        evaluation = evaluate_live_evidence(sources, now=now)
        diagnostics.extend(evaluation.diagnostics)
        live_evidence = evaluation.freshness

    ordered = tuple(
        sorted(
            diagnostics,
            key=lambda item: (item.code, item.subject_kind, item.subject_id, item.path or ""),
        )
    )
    errors = sum(item.severity == "error" for item in ordered)
    warnings = sum(item.severity == "warning" for item in ordered)
    gaps = sum(item.severity == "capability_gap" for item in ordered)
    return FleetValidationResult(
        valid=errors == 0 and gaps == 0 and (not strict or warnings == 0),
        mode="live" if live else "static",
        diagnostics=ordered,
        summary=FleetValidationSummary(
            host_count=len(active_hosts),
            error_count=errors,
            warning_count=warnings,
            capability_gap_count=gaps,
        ),
        live_evidence=live_evidence,
    )


def _validate_unique_hosts(hosts: list[Host], diagnostics: list[FleetValidationDiagnostic]) -> None:
    for attribute, code, label in (
        ("canonical_name", "duplicate_canonical_name", "canonical name"),
        ("tailscale_name", "duplicate_tailscale_name", "Tailnet name"),
    ):
        seen: set[str] = set()
        for host in hosts:
            value = getattr(host, attribute)
            if value is None:
                continue
            if value in seen:
                diagnostics.append(
                    FleetValidationDiagnostic(
                        code=code,
                        severity="error",
                        message=f"Active hosts share declared {label}",
                        subject_kind="host",
                        subject_id=host.canonical_name,
                        path=f"hosts.{host.uuid}.{attribute}",
                    )
                )
            else:
                seen.add(value)


def _validate_host(
    host: Host,
    catalog: RoleServiceCatalog,
    diagnostics: list[FleetValidationDiagnostic],
    registry_root: Path,
) -> None:
    roles = host.roles
    overrides = host.role_overrides
    services = set(host.services)
    name = host.canonical_name
    host_id = host.uuid
    compose_services = _compose_services(host, registry_root, diagnostics)
    for role_name in roles:
        role = catalog.roles.get(role_name)
        if role is None:
            diagnostics.append(
                _host_diagnostic(
                    "unknown_role",
                    "error",
                    "Host declares an unknown role",
                    name,
                    host_id,
                    role_name,
                )
            )
            continue
        override = overrides.get(role_name, {})
        for parameter in role.requires_params:
            if parameter not in override:
                diagnostics.append(
                    _host_diagnostic(
                        "role_parameter_missing",
                        "error",
                        "Role is missing a required parameter",
                        name,
                        host_id,
                        role_name,
                    )
                )
        for dependency in role.requires_roles:
            if dependency not in roles:
                diagnostics.append(
                    _host_diagnostic(
                        "role_dependency_missing",
                        "error",
                        "Role is missing a required role dependency",
                        name,
                        host_id,
                        role_name,
                    )
                )
        if (
            compose_services is not None
            and role.compose_service
            and role.compose_service not in compose_services
        ):
            diagnostics.append(
                _host_diagnostic(
                    "role_compose_service_missing",
                    "error",
                    "Role requires an undeclared compose service",
                    name,
                    host_id,
                    role_name,
                )
            )
    for service_name in services:
        definition = catalog.services.get(service_name)
        if definition is None:
            diagnostics.append(
                _host_diagnostic(
                    "unknown_service",
                    "warning",
                    "Host declares a service absent from the catalog",
                    name,
                    host_id,
                    service_name,
                )
            )
        elif (
            compose_services is not None
            and definition.compose_service
            and definition.compose_service not in compose_services
        ):
            diagnostics.append(
                _host_diagnostic(
                    "service_compose_service_missing",
                    "warning",
                    "Service requires an undeclared compose service",
                    name,
                    host_id,
                    service_name,
                )
            )


def _compose_services(
    host: Host, registry_root: Path, diagnostics: list[FleetValidationDiagnostic]
) -> set[str] | None:
    path = registry_root / "hosts" / host.uuid / "docker-compose.yml.j2"
    if not path.is_file():
        diagnostics.append(
            FleetValidationDiagnostic(
                code="compose_template_missing",
                severity="error",
                message="Active host has no declared Compose template",
                subject_kind="host",
                subject_id=host.canonical_name,
                path=f"hosts.{host.uuid}.docker-compose.yml.j2",
            )
        )
        return None
    try:
        return _static_compose_service_names(_expand_literal_includes(path, registry_root))
    except _ComposeIncludeError as error:
        diagnostics.append(
            FleetValidationDiagnostic(
                code=error.code,
                severity="capability_gap",
                message=error.message,
                subject_kind="host",
                subject_id=host.canonical_name,
                path=f"hosts.{host.uuid}.docker-compose.yml.j2",
            )
        )
        return None


def _expand_literal_includes(
    path: Path,
    registry_root: Path,
    seen: frozenset[Path] = frozenset(),
) -> str:
    """Inline quoted local includes without evaluating Jinja expressions."""
    registry_root = registry_root.resolve()
    path = path.resolve()
    if not path.is_relative_to(registry_root):
        raise _ComposeIncludeError(
            "compose_template_include_unsafe",
            "Compose template includes must remain within the selected Registry",
        )
    template_root = (registry_root / "hosts" / "_templates").resolve()
    if path in seen:
        raise _ComposeIncludeError(
            "compose_template_include_cycle",
            "Compose template includes contain a cycle",
        )
    if len(seen) >= _MAX_LITERAL_INCLUDE_DEPTH:
        raise _ComposeIncludeError(
            "compose_template_include_depth_exceeded",
            "Compose template includes exceed the static expansion depth limit",
        )
    source = path.read_text(encoding="utf-8")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        local_root = path.parent.resolve()
        candidates = ((local_root / name).resolve(), (template_root / name).resolve())
        safe_candidates = (
            candidate
            for candidate, root in zip(candidates, (local_root, template_root), strict=True)
            if candidate.is_relative_to(root)
        )
        candidate = next((item for item in safe_candidates if item.is_file()), None)
        if candidate is None:
            if not any(
                candidate.is_relative_to(root)
                for candidate, root in zip(candidates, (local_root, template_root), strict=True)
            ):
                raise _ComposeIncludeError(
                    "compose_template_include_unsafe",
                    "Compose template includes must remain within the including directory or hosts/_templates",
                )
            raise _ComposeIncludeError(
                "compose_template_include_unresolved",
                "Compose template includes cannot be statically resolved",
            )
        return _expand_literal_includes(candidate, registry_root, seen | {path})

    expanded = _LITERAL_INCLUDE_PATTERN.sub(replace, source)
    if _INCLUDE_PATTERN.search(expanded):
        raise _ComposeIncludeError(
            "compose_template_include_unresolved",
            "Compose template includes must use a literal path for static validation",
        )
    return expanded


def _static_compose_service_names(source: str) -> set[str]:
    """Extract literal service keys without evaluating Jinja template expressions."""
    names: set[str] = set()
    in_services = False
    for line in source.splitlines():
        if re.fullmatch(r"\s*services:\s*", line):
            in_services = True
            continue
        if in_services and line and not line[0].isspace():
            break
        match = re.fullmatch(r"\s{2}([A-Za-z0-9_-]+):.*", line)
        if in_services and match:
            names.add(match.group(1))
    return names


def _host_diagnostic(
    code: str,
    severity: Literal["error", "warning"],
    message: str,
    name: str,
    host_id: str,
    item: str,
) -> FleetValidationDiagnostic:
    return FleetValidationDiagnostic(
        code=code,
        severity=severity,
        message=message,
        subject_kind="host",
        subject_id=name,
        path=f"hosts.{host_id}.{item}",
    )


def _validate_database_edges(
    sources: LoadedSources, diagnostics: list[FleetValidationDiagnostic]
) -> None:
    for edge in sources.edges:
        if edge.protocol not in _DB_PROTOCOLS:
            continue
        auth = edge.to_dict().get("auth", {})
        role = auth.get("role")
        database = auth.get("database")
        username = auth.get("username")
        secret_ref = auth.get("secret_ref")
        if role not in _DB_ROLES:
            _edge_error(diagnostics, edge.id, "Database edge has an invalid auth role")
            continue
        prefix = "mariadb" if edge.protocol in {"mysql", "mariadb"} else "postgresql"
        if role in _DB_SCOPED_ROLES:
            expected_user = f"{role}_{database}" if database else None
            expected_secret = f"{prefix}_{role}_password_{database}" if database else None
        else:
            expected_user = _DB_GLOBAL_USERS[role]
            expected_secret = f"{prefix}_{role}_password"
        if not expected_user or username != expected_user or secret_ref != expected_secret:
            _edge_error(
                diagnostics,
                edge.id,
                "Database edge auth does not match the declared naming convention",
            )


def _edge_error(diagnostics: list[FleetValidationDiagnostic], edge_id: str, message: str) -> None:
    diagnostics.append(
        FleetValidationDiagnostic(
            code="database_edge_auth_invalid",
            severity="error",
            message=message,
            subject_kind="edge",
            subject_id=edge_id,
            path=f"edges.{edge_id}.auth",
        )
    )
