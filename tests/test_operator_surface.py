from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agent_surface import OperationError
from click.testing import CliRunner

from infralink.cli.contracts import DoctorTarget, HostBootstrapPlanResult, HostReadinessResult
from infralink.cli.errors import CliFailure
from infralink.cli.main import _bootstrap_tailnet_address, _raise_cli_operation_error, cli
from infralink.operator_surface import (
    DoctorBootstrapPlanRequest,
    HostBootstrapRequest,
    doctor_host_bootstrap_plan,
    operator_click_adapter,
    operator_surface,
)


@pytest.mark.parametrize(
    ("operation_code", "expected_code", "expected_exit"),
    [
        ("provider_timeout", "provider_timeout", 4),
        ("provider_authentication_failed", "provider_authentication_failed", 4),
        ("source_not_found", "input_load_failed", 3),
        ("source_invalid", "input_load_failed", 3),
        ("internal_error", "internal_error", 70),
        ("unrecognized_operation_error", "internal_error", 70),
    ],
)
def test_cli_operation_error_bridge_preserves_provider_and_source_taxonomy(
    operation_code: str, expected_code: str, expected_exit: int
) -> None:
    with pytest.raises(CliFailure) as captured:
        _raise_cli_operation_error(OperationError(operation_code, "expected failure"))

    assert captured.value.code.value == expected_code
    assert captured.value.exit_code == expected_exit


def test_bootstrap_request_uses_released_sensitive_stdin_contract() -> None:
    request = HostBootstrapRequest(
        registry=Path("/registry"),
        host_id="host-1",
        ssh_host="100.64.0.1",
        apply=True,
        bws_token="token",
    )

    assert request.bws_token == "token"
    assert HostBootstrapRequest.model_fields["bws_token"].json_schema_extra == {
        "sensitive": True,
        "cli": {"source": "stdin", "max_bytes": 8192},
    }


def test_generated_click_uses_canonical_bootstrap_flags_and_redacts_stdin_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The generated adapter is the public canonical bootstrap parser."""
    from infralink.operator_operations import host_bootstrap

    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    tailscale_ip: 100.64.0.1\n",
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    def fake_execute_bootstrap(
        context: object, request: HostBootstrapRequest
    ) -> tuple[object, list[object], bool]:
        observed["registry"] = context.registry_path  # type: ignore[attr-defined]
        observed["request"] = request
        return (
            HostBootstrapPlanResult(
                host=DoctorTarget(type="host", id=host_id, canonical_name="host-1"),
                readiness=HostReadinessResult(
                    transport="root_ssh", ready=False, checks=[], actions=[]
                ),
            ),
            [],
            False,
        )

    monkeypatch.setattr(host_bootstrap, "execute_bootstrap", fake_execute_bootstrap)
    token = "never-render-this-token"
    result = CliRunner().invoke(
        operator_click_adapter().command(),
        [
            "--registry",
            str(registry),
            "host",
            "bootstrap",
            host_id,
            "--ssh-host",
            "100.64.0.1",
            "--apply",
            "--bws-token-stdin",
            "--format",
            "json",
        ],
        input=f"{token}\n",
    )

    assert result.exit_code == 0, result.output
    assert "--plan-only" not in result.output
    assert "--apply-changes" not in result.output
    assert token not in result.output
    payload = json.loads(result.output)
    assert payload["command"]["parsed"]["path"] == ["host", "bootstrap"]
    assert payload["command"]["parsed"]["flags"] == [
        "--registry",
        "--ssh-host",
        "--apply",
        "--bws-token-stdin",
        "--format",
    ]
    request = observed["request"]
    assert isinstance(request, HostBootstrapRequest)
    assert request.apply is True
    assert request.plan is False
    assert request.bws_token == token


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


def test_bootstrap_plan_uses_one_typed_transport_boundary_across_doctor_and_click(
    tmp_path: Path,
) -> None:
    """The retained Doctor and Click adapters reject a mismatched transport alike."""
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
