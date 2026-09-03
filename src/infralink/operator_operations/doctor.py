"""Transport-neutral building blocks for the public doctor operation."""

from __future__ import annotations

import os
from ipaddress import ip_address, ip_network
from pathlib import Path

from agent_surface import OperationError
from pydantic import BaseModel, ConfigDict, Field

from infralink.operator_sources import SourceRequest

OBSERVATION_PLAN_ENVVAR = "INFRALINK_OBSERVATION_PLAN"
ADAPTER_BINDINGS_ENVVAR = "INFRALINK_ADAPTER_BINDINGS"
GATUS_URL_ENVVAR = "INFRALINK_GATUS_URL"


class DoctorBootstrapPlanRequest(SourceRequest):
    """Inputs needed to plan a declared host bootstrap."""

    host_ref: str = Field(min_length=1)
    ssh_host: str = Field(min_length=1)
    declared_ssh_host: str = Field(min_length=1)


class DoctorBootstrapPlanResult(BaseModel):
    """The bounded command transition for a bootstrap plan."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    argv: tuple[str, ...]
    ssh_host: str


def doctor_host_bootstrap_plan(request: DoctorBootstrapPlanRequest) -> DoctorBootstrapPlanResult:
    """Validate and construct the one executable bootstrap-plan transition."""
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
        argv=("host", "bootstrap", request.host_ref, "--ssh-host", request.ssh_host, "--plan"),
        ssh_host=request.ssh_host,
    )


def resolve_doctor_inputs(
    observation_plan: Path | None,
    adapter_bindings: Path | None,
    gatus_url: str | None,
) -> tuple[Path | None, Path | None, str | None]:
    """Apply the established explicit-input then environment precedence once."""
    return (
        observation_plan or _configured_path(OBSERVATION_PLAN_ENVVAR),
        adapter_bindings or _configured_path(ADAPTER_BINDINGS_ENVVAR),
        gatus_url if gatus_url is not None else os.environ.get(GATUS_URL_ENVVAR),
    )


def _configured_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value).expanduser() if value else None


def _is_tailnet_ipv4(address: str) -> bool:
    try:
        return ip_address(address) in ip_network("100.64.0.0/10")
    except ValueError:
        return False
