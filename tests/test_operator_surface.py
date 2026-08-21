from __future__ import annotations

import asyncio

import pytest
from agent_surface import OperationError

from infralink.cli.errors import CliFailure
from infralink.cli.main import _bootstrap_tailnet_address
from infralink.operator_surface import (
    HostBootstrapTransportRequest,
    operator_surface,
    validate_bootstrap_transport,
)


def test_bootstrap_plan_operation_has_one_typed_tailnet_contract() -> None:
    result = asyncio.run(
        operator_surface.invoke(
            "doctor.host.bootstrap_plan",
            {"host_ref": "host-1", "ssh_host": "100.64.0.1"},
        )
    )

    assert result.argv == ("host", "bootstrap", "host-1", "--ssh-host", "100.64.0.1", "--plan")


def test_bootstrap_plan_operation_rejects_non_tailnet_addresses() -> None:
    with pytest.raises(OperationError, match="Tailnet IPv4"):
        asyncio.run(
            operator_surface.invoke(
                "doctor.host.bootstrap_plan",
                {"host_ref": "host-1", "ssh_host": "192.0.2.1"},
            )
        )


def test_click_bootstrap_and_typed_operation_share_transport_acceptance() -> None:
    target = type("Target", (), {"uuid": "host-1", "tailscale_ip": "100.64.0.1"})()
    request = HostBootstrapTransportRequest(
        host_ref="host-1",
        ssh_host="100.64.0.1",
        declared_ssh_host="100.64.0.1",
    )

    assert validate_bootstrap_transport(request) == "100.64.0.1"
    assert _bootstrap_tailnet_address(target, "100.64.0.1") == "100.64.0.1"

    with pytest.raises(OperationError, match="exactly match"):
        validate_bootstrap_transport(request.model_copy(update={"ssh_host": "100.64.0.2"}))
    with pytest.raises(CliFailure, match="exactly match"):
        _bootstrap_tailnet_address(target, "100.64.0.2")
