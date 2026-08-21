from __future__ import annotations

import asyncio

import pytest
from agent_surface import OperationError

from infralink.operator_surface import operator_surface


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
