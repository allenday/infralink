"""Typed operator operations shared by future Click and MCP projections.

The existing CLI envelope remains public API.  New operator transitions enter
through this registry so Click and MCP do not acquire independent schemas.
"""

from __future__ import annotations

from ipaddress import ip_address, ip_network

from agent_surface import App, OperationError  # type: ignore[import-untyped]
from pydantic import BaseModel, ConfigDict, Field


class _OperationModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DoctorBootstrapPlanRequest(_OperationModel):
    host_ref: str = Field(min_length=1)
    ssh_host: str = Field(min_length=1)


class DoctorBootstrapPlanResult(_OperationModel):
    argv: tuple[str, ...]


operator_surface = App("infralink")


@operator_surface.operation(
    "doctor.host.bootstrap_plan",
    summary="Plan declared host bootstrap prerequisites",
    read_only=True,
)  # type: ignore[untyped-decorator]
def doctor_host_bootstrap_plan(request: DoctorBootstrapPlanRequest) -> DoctorBootstrapPlanResult:
    """Build the only executable bootstrap-plan transition for a Tailnet host."""
    if not _is_tailnet_ipv4(request.ssh_host):
        raise OperationError(
            "tailnet_address_required",
            "Host bootstrap requires a declared Tailnet IPv4 address",
            fix="Declare a 100.64.0.0/10 host address before planning bootstrap.",
        )
    return DoctorBootstrapPlanResult(
        argv=("host", "bootstrap", request.host_ref, "--ssh-host", request.ssh_host, "--plan")
    )


def _is_tailnet_ipv4(address: str) -> bool:
    try:
        return ip_address(address) in ip_network("100.64.0.0/10")
    except ValueError:
        return False
