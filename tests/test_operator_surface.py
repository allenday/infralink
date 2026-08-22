from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agent_surface import OperationError
from click.testing import CliRunner
from mcp import Client

from infralink.cli.errors import CliFailure
from infralink.cli.main import _bootstrap_tailnet_address, cli
from infralink.mcp_server import create_server
from infralink.operator_surface import (
    DoctorBootstrapPlanRequest,
    HostBootstrapRequest,
    doctor_host_bootstrap_plan,
    operator_surface,
)


def test_bootstrap_request_uses_released_sensitive_stdin_contract() -> None:
    request = HostBootstrapRequest(
        registry=Path("/registry"),
        host_id="host-1",
        ssh_host="100.64.0.1",
        apply_changes=True,
        bws_token="token",
    )

    assert request.bws_token == "token"
    assert HostBootstrapRequest.model_fields["bws_token"].json_schema_extra == {
        "sensitive": True,
        "cli": {"source": "stdin", "max_bytes": 8192},
    }


def test_bootstrap_plan_operation_has_one_typed_tailnet_contract() -> None:
    result = asyncio.run(
        operator_surface.invoke(
            "doctor.host.bootstrap_plan",
            {
                "host_ref": "host-1",
                "ssh_host": "100.64.0.1",
                "declared_ssh_host": "100.64.0.1",
            },
        )
    )

    assert result.argv == ("host", "bootstrap", "host-1", "--ssh-host", "100.64.0.1", "--plan")


def test_bootstrap_plan_operation_rejects_non_tailnet_addresses() -> None:
    with pytest.raises(OperationError, match="Tailnet IPv4"):
        asyncio.run(
            operator_surface.invoke(
                "doctor.host.bootstrap_plan",
                {
                    "host_ref": "host-1",
                    "ssh_host": "192.0.2.1",
                    "declared_ssh_host": "192.0.2.1",
                },
            )
        )


def test_click_bootstrap_and_typed_operation_share_transport_acceptance() -> None:
    target = type("Target", (), {"uuid": "host-1", "tailscale_ip": "100.64.0.1"})()
    request = DoctorBootstrapPlanRequest(
        host_ref="host-1",
        ssh_host="100.64.0.1",
        declared_ssh_host="100.64.0.1",
    )

    assert doctor_host_bootstrap_plan(request).ssh_host == "100.64.0.1"
    assert _bootstrap_tailnet_address(target, "100.64.0.1") == "100.64.0.1"

    with pytest.raises(OperationError, match="exactly match"):
        doctor_host_bootstrap_plan(request.model_copy(update={"ssh_host": "100.64.0.2"}))
    with pytest.raises(CliFailure, match="exactly match"):
        _bootstrap_tailnet_address(target, "100.64.0.2")


def test_bootstrap_plan_uses_one_typed_transport_boundary_across_adapters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Doctor, Click, and MCP must reject one mismatched declared transport alike."""
    host_id = "11111111-1111-4111-8111-111111111111"
    declared_address = "100.64.0.1"
    requested_address = "100.64.0.2"
    registry = tmp_path / "hosts"
    manifest = registry / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    tailscale_ip: {declared_address}\n",
        encoding="utf-8",
    )

    with pytest.raises(OperationError, match="exactly match"):
        asyncio.run(
            operator_surface.invoke(
                "doctor.host.bootstrap_plan",
                {
                    "host_ref": host_id,
                    "ssh_host": requested_address,
                    "declared_ssh_host": declared_address,
                },
            )
        )

    click_result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(registry),
            "host",
            "bootstrap",
            host_id,
            "--ssh-host",
            requested_address,
            "--plan",
        ],
    )
    assert click_result.exit_code == 3
    click_payload = json.loads(click_result.output)
    assert click_payload["error"]["code"] == "configuration_required"
    assert click_payload["error"]["details"] == {
        "host": host_id,
        "declared_tailscale_ip": declared_address,
    }

    monkeypatch.setenv("INFRALINK_REGISTRY", str(registry))

    async def exercise_mcp() -> None:
        async with Client(create_server()) as client:
            mcp_result = await client.call_tool(
                "infralink_host_bootstrap",
                {
                    "host_id": host_id,
                    "ssh_host": requested_address,
                    "plan": True,
                },
            )

        assert mcp_result.is_error is True
        assert mcp_result.structured_content["error"] == click_payload["error"]

    asyncio.run(exercise_mcp())
