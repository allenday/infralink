"""Typed operator operations projected through Infralink's public adapters.

Every public transport consumes these Pydantic contracts; transport code must
not introduce an independent validation path.
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network

from agent_surface import App, OperationError
from pydantic import BaseModel, ConfigDict, Field, model_validator

from infralink.cli.contracts import (
    EdgeListResult,
    HostListResult,
    InfoResult,
    InfoSources,
    InfoSummary,
    ServiceListResult,
)
from infralink.cli.queries import list_edges, list_hosts, list_services
from infralink.operator_sources import SourceRequest, load_registry, load_sources


class _OperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DoctorBootstrapPlanRequest(_OperationModel):
    host_ref: str = Field(min_length=1)
    ssh_host: str = Field(min_length=1)
    declared_ssh_host: str = Field(min_length=1)


class DoctorBootstrapPlanResult(_OperationModel):
    argv: tuple[str, ...]
    ssh_host: str


operator_surface = App("infralink")


class HostListRequest(SourceRequest):
    """List hosts from one explicit registry source."""


class ServiceListRequest(SourceRequest):
    """List logical services from one explicit registry source."""


class EdgeListRequest(SourceRequest):
    """List declared edges from one explicit registry source."""


class InfoRequest(SourceRequest):
    """Summarize one explicit registry source."""


class HostBootstrapRequest(SourceRequest):
    """Transport-neutral input for one declared host bootstrap attempt."""

    host_id: str = Field(min_length=1, json_schema_extra={"cli": {"kind": "argument"}})
    ssh_host: str = Field(min_length=1)
    plan_only: bool = False
    apply_changes: bool = False
    bws_token: str | None = Field(
        default=None,
        min_length=1,
        json_schema_extra={
            "sensitive": True,
            "cli": {"source": "stdin", "max_bytes": 8192},
        },
    )

    @model_validator(mode="after")
    def validate_mode(self) -> HostBootstrapRequest:
        if self.plan_only and self.apply_changes:
            raise ValueError("pass at most one of plan_only or apply_changes")
        if self.apply_changes and self.bws_token is None:
            raise ValueError("apply_changes requires bws_token")
        return self


@operator_surface.operation("host.list", summary="List declared hosts", read_only=True)  # type: ignore[untyped-decorator]
def host_list(request: HostListRequest) -> HostListResult:
    """Return the registry host list without a Click context."""
    return list_hosts(load_registry(request).registry)


@operator_surface.operation("service.list", summary="List declared services", read_only=True)  # type: ignore[untyped-decorator]
def service_list(request: ServiceListRequest) -> ServiceListResult:
    """Return the registry service list without a Click context."""
    sources = load_sources(request)
    return list_services(sources.registry, sources.edges)


@operator_surface.operation("edge.list", summary="List declared edges", read_only=True)  # type: ignore[untyped-decorator]
def edge_list(request: EdgeListRequest) -> EdgeListResult:
    """Return the selected registry edge list without a Click context."""
    return list_edges(load_sources(request).edges)


@operator_surface.operation("info", summary="Summarize declared topology", read_only=True)  # type: ignore[untyped-decorator]
def info(request: InfoRequest) -> InfoResult:
    """Return topology counts and the exact resolved declaration sources."""
    sources = load_sources(request)
    return InfoResult(
        sources=InfoSources(registry=str(sources.registry_path), edges=str(sources.edges_path)),
        summary=InfoSummary(
            host_count=len(sources.registry),
            service_count=len(list_services(sources.registry, sources.edges).items),
            edge_count=len(sources.edges),
        ),
    )


@operator_surface.operation(
    "doctor.host.bootstrap_plan",
    summary="Plan declared host bootstrap prerequisites",
    read_only=True,
)  # type: ignore[untyped-decorator]
def doctor_host_bootstrap_plan(request: DoctorBootstrapPlanRequest) -> DoctorBootstrapPlanResult:
    """Validate and build the only executable bootstrap-plan transition."""
    if not _is_tailnet_ipv4(request.declared_ssh_host):
        raise OperationError(
            "tailnet_address_required",
            "Host bootstrap requires a declared Tailnet IPv4 address",
            details=({"host": request.host_ref},),
            fix="Declare a 100.64.0.0/10 host address before planning bootstrap.",
        )
    if request.ssh_host != request.declared_ssh_host:
        raise OperationError(
            "bootstrap_transport_mismatch",
            "Bootstrap SSH host must exactly match the declared Tailnet IPv4",
            details=(
                {"host": request.host_ref, "declared_tailscale_ip": request.declared_ssh_host},
            ),
            fix="Pass the registry tailscale_ip with --ssh-host.",
        )
    return DoctorBootstrapPlanResult(
        argv=(
            "host",
            "bootstrap",
            request.host_ref,
            "--ssh-host",
            request.ssh_host,
            "--plan",
        ),
        ssh_host=request.ssh_host,
    )


def _is_tailnet_ipv4(address: str) -> bool:
    try:
        return ip_address(address) in ip_network("100.64.0.0/10")
    except ValueError:
        return False
