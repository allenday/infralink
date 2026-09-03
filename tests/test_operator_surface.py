from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
from agent_surface import OperationError
from click.testing import CliRunner
from mcp import Client

from infralink.cli.contracts import DoctorTarget, HostBootstrapPlanResult, HostReadinessResult
from infralink.cli.errors import CliFailure
from infralink.cli.main import _bootstrap_tailnet_address, _raise_cli_operation_error, cli
from infralink.cli.operations import OperationRecord
from infralink.operator_operations.doctor import DoctorBootstrapPlanRequest
from infralink.operator_surface import (
    ExplainRequest,
    HostBootstrapRequest,
    HostCreateRequest,
    OperationStatusRequest,
    RegistryHostGetRequest,
    RegistryHostPatchRequest,
    doctor_host_bootstrap_plan,
    explain_operation,
    host_create_operation,
    observation_surface,
    operation_status_operation,
    operator_click_adapter,
    operator_mcp_adapter,
    operator_surface,
    registry_host_get_operation,
    registry_host_patch_operation,
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

    assert result.exit_code == 1, result.output
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


def test_bootstrap_plan_has_one_typed_tailnet_contract() -> None:
    result = doctor_host_bootstrap_plan(
        DoctorBootstrapPlanRequest(
            host_ref="host-1",
            ssh_host="100.64.0.1",
            declared_ssh_host="100.64.0.1",
        )
    )

    assert result.argv == ("host", "bootstrap", "host-1", "--ssh-host", "100.64.0.1", "--plan")


def test_bootstrap_plan_operation_rejects_non_tailnet_addresses() -> None:
    with pytest.raises(OperationError, match="Tailnet IPv4"):
        doctor_host_bootstrap_plan(
            DoctorBootstrapPlanRequest(
                host_ref="host-1",
                ssh_host="192.0.2.1",
                declared_ssh_host="192.0.2.1",
            )
        )


def test_doctor_operation_uses_the_retained_cli_evaluator_without_live_io(tmp_path: Path) -> None:
    """Typed doctor shares declaration-only evidence behavior with the Click leaf."""
    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    tailscale_ip: 100.64.0.1\n",
        encoding="utf-8",
    )
    (registry / "edges.yml").write_text("edges: []\n", encoding="utf-8")
    observation = registry / "operations" / "observation"
    observation.mkdir(parents=True)
    (observation / "core-plan.json").write_text(
        '{"schema_version":"infralink.observation-plan/v1","dependencies":[]}',
        encoding="utf-8",
    )
    (observation / "adapter-bindings.yml").write_text(
        "schema_version: infra-observe.adapter-bindings.v2\nbindings: []\n",
        encoding="utf-8",
    )

    result = asyncio.run(
        operator_surface.invoke("doctor", {"registry": str(registry), "declaration_only": True})
    )

    assert result.target.type == "global"
    assert result.declared == {"host_count": 1, "service_count": 0, "edge_count": 0}
    assert result.status == "unknown"
    assert result.reason == "no_live_observation_evidence"

    cli_result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["--registry", str(registry), "doctor", "--validate", "--format", "json"],
    )
    assert cli_result.exit_code == 0, cli_result.output
    payload = json.loads(cli_result.output)
    assert [item["rel"] for item in payload["next_actions"]] == ["help", "list"]

    missing_result = CliRunner().invoke(
        operator_click_adapter().command(),
        [
            "--registry",
            str(registry),
            "doctor",
            "--validate",
            "--target-type",
            "host",
            "--target-ref",
            "missing",
            "--format",
            "json",
        ],
    )
    assert missing_result.exit_code == 3, missing_result.output
    missing_payload = json.loads(missing_result.output)
    assert missing_payload["error"]["code"] == "entity_not_found"
    assert missing_payload["next_actions"][0]["command"].endswith("host list")


def test_doctor_is_registered_once_for_the_typed_mcp_projection() -> None:
    async def list_tools() -> set[str]:
        async with Client(operator_mcp_adapter().server) as client:
            tools = await client.list_tools()
        return {tool.name for tool in tools.tools}

    assert "doctor" in asyncio.run(list_tools())
    assert "doctor.host.bootstrap_plan" not in asyncio.run(list_tools())


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
    registry = tmp_path
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    tailscale_ip: {declared_address}\n",
        encoding="utf-8",
    )

    with pytest.raises(OperationError, match="exactly match"):
        doctor_host_bootstrap_plan(
            DoctorBootstrapPlanRequest(
                host_ref=host_id,
                ssh_host=requested_address,
                declared_ssh_host=declared_address,
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


def test_host_create_operation_uses_checkout_root_and_refuses_runtime_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    checkout = tmp_path / "authoring"
    (checkout / "hosts").mkdir(parents=True)

    dry_run = host_create_operation(HostCreateRequest(name="new-node", address="100.64.1.9"))
    assert dry_run.mode == "dry_run"
    assert dry_run.manifest_path is None
    assert dry_run.manifest["hosts"][dry_run.host_id]["tailscale_ip"] == "100.64.1.9"

    written = host_create_operation(
        HostCreateRequest(
            registry=checkout,
            name="new-node.internal",
            address="new-node.internal",
            write=True,
        )
    )
    assert written.mode == "written"
    assert written.git_worktree == checkout
    assert written.manifest_path == checkout / "hosts" / written.host_id / "manifest.yml"
    assert written.manifest_path.is_file()

    monkeypatch.setattr(
        "infralink.operator_operations.host_authoring.managed_runtime_registry_root",
        lambda: checkout,
    )
    with pytest.raises(OperationError, match="operator registry working tree"):
        host_create_operation(
            HostCreateRequest(
                registry=checkout,
                name="blocked-node",
                address="100.64.1.10",
                write=True,
            )
        )


def test_host_create_is_discoverable_through_the_typed_mcp_adapter() -> None:
    async def invoke() -> tuple[set[str], dict[str, object]]:
        async with Client(operator_mcp_adapter().server) as client:
            tools = await client.list_tools()
            result = await client.call_tool(
                "host.create", {"name": "new-node", "address": "100.64.1.9"}
            )
        assert result.is_error is False
        return {tool.name for tool in tools.tools}, result.structured_content

    tools, payload = asyncio.run(invoke())
    assert "host.create" in tools
    assert payload["result"]["mode"] == "dry_run"


def test_registry_host_authoring_operations_preserve_preview_then_explicit_write(
    tmp_path: Path,
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {host_id}:\n"
        "    canonical_name: host-1\n"
        "    status: provisioning\n"
        "    tailscale_ip: 100.64.1.9\n"
        "    controller_bootstrap:\n"
        "      controller_image: ghcr.io/example/controller:main\n",
        encoding="utf-8",
    )

    shown = registry_host_get_operation(
        RegistryHostGetRequest(registry=registry, host_ref="host-1")
    )
    assert shown.host.id == host_id
    assert shown.manifest_path == str(manifest)

    preview = registry_host_patch_operation(
        RegistryHostPatchRequest(
            registry=registry,
            host_ref="host-1",
            assignments=(
                "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.6.25",
            ),
        )
    )
    assert preview.mode == "preview"
    assert "v0.6.25" not in manifest.read_text(encoding="utf-8")

    written = registry_host_patch_operation(
        RegistryHostPatchRequest(
            registry=registry,
            host_ref="host-1",
            assignments=(
                "controller_bootstrap.controller_image=ghcr.io/example/controller:v0.6.25",
            ),
            write=True,
        )
    )
    assert written.mode == "written"
    assert "v0.6.25" in manifest.read_text(encoding="utf-8")

    action_result = CliRunner().invoke(
        operator_click_adapter().command(),
        ["--registry", str(registry), "registry", "host", "get", "host-1", "--format", "json"],
    )
    assert action_result.exit_code == 0, action_result.output
    action_payload = json.loads(action_result.output)
    assert action_payload["next_actions"][0]["rel"] == "patch"
    assert f"--registry {registry}" in action_payload["next_actions"][0]["command"]
    assert action_payload["next_actions"][0]["command"].endswith("--set '{assignment}'")

    preview_action = CliRunner().invoke(
        operator_click_adapter().command(),
        [
            "--registry",
            str(registry),
            "registry",
            "host",
            "patch",
            "host-1",
            "--set",
            "status=active",
            "--format",
            "json",
        ],
    )
    assert preview_action.exit_code == 0, preview_action.output
    preview_payload = json.loads(preview_action.output)
    assert preview_payload["next_actions"][0]["rel"] == "write"
    assert f"--registry {registry}" in preview_payload["next_actions"][0]["command"]
    assert preview_payload["next_actions"][0]["command"].endswith("status=active --write")


def test_registry_host_patch_refuses_the_managed_runtime_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    registry = tmp_path / "registry"
    manifest = registry / "hosts" / host_id / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {host_id}:\n"
        "    canonical_name: host-1\n"
        "    status: provisioning\n"
        "    tailscale_ip: 100.64.1.9\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "infralink.cli.registry_authoring.operator_sources.managed_runtime_registry_root",
        lambda: registry,
    )

    with pytest.raises(OperationError, match="managed runtime checkout"):
        registry_host_patch_operation(
            RegistryHostPatchRequest(
                registry=registry,
                host_ref="host-1",
                assignments=("status=active",),
                write=True,
            )
        )


def test_registry_host_authoring_rejects_hosts_and_manifest_symlink_escapes(tmp_path: Path) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    outside = tmp_path / "outside"
    outside_manifest = outside / host_id / "manifest.yml"
    outside_manifest.parent.mkdir(parents=True)
    outside_manifest.write_text(
        f"hosts:\n  {host_id}:\n    canonical_name: host-1\n    status: provisioning\n",
        encoding="utf-8",
    )

    escaped_hosts = tmp_path / "escaped-hosts"
    escaped_hosts.mkdir()
    (escaped_hosts / "hosts").symlink_to(outside, target_is_directory=True)
    with pytest.raises(OperationError, match="contained hosts directory"):
        registry_host_get_operation(
            RegistryHostGetRequest(registry=escaped_hosts, host_ref="host-1")
        )

    registry = tmp_path / "registry"
    hosts = registry / "hosts"
    hosts.mkdir(parents=True)
    host_directory = hosts / host_id
    host_directory.mkdir()
    (host_directory / "manifest.yml").symlink_to(outside_manifest)
    with pytest.raises(OperationError, match="escapes the selected checkout"):
        registry_host_get_operation(RegistryHostGetRequest(registry=registry, host_ref="host-1"))


def test_host_create_refuses_a_hosts_symlink_into_the_managed_runtime_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    runtime = tmp_path / "runtime"
    runtime_hosts = runtime / "hosts"
    runtime_hosts.mkdir(parents=True)
    authoring = tmp_path / "authoring"
    authoring.mkdir()
    (authoring / "hosts").symlink_to(runtime_hosts, target_is_directory=True)
    monkeypatch.setattr(
        "infralink.operator_operations.host_authoring.managed_runtime_registry_root",
        lambda: runtime,
    )

    with pytest.raises(OperationError, match="must not resolve inside the managed runtime"):
        host_create_operation(
            HostCreateRequest(
                registry=authoring,
                name="blocked-node",
                address="100.64.1.10",
                write=True,
            )
        )

    assert list(runtime_hosts.iterdir()) == []


def test_host_create_refuses_a_generated_host_directory_symlink(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    authoring = tmp_path / "authoring"
    hosts = authoring / "hosts"
    hosts.mkdir(parents=True)
    outside = tmp_path / "outside" / host_id
    (hosts / host_id).symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr("infralink.operator_operations.host_authoring.uuid4", lambda: UUID(host_id))

    with pytest.raises(OperationError, match="must not resolve inside the managed runtime"):
        host_create_operation(
            HostCreateRequest(
                registry=authoring,
                name="blocked-node",
                address="100.64.1.10",
                write=True,
            )
        )

    assert not outside.exists()


def test_operation_status_uses_one_typed_provider_contract(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    host_id = "11111111-1111-4111-8111-111111111111"
    invocation = "a" * 32
    operation_id = f"ssh/{host_id}/{invocation}"
    target = SimpleNamespace(uuid=host_id, canonical_name="host-1")
    sources = SimpleNamespace(
        registry_path=tmp_path,
        registry=SimpleNamespace(get=lambda reference: target if reference == host_id else None),
    )
    provider = SimpleNamespace(
        status=lambda requested_id, request: OperationRecord(
            id=requested_id,
            state="converged",
            target={"type": "host", "id": host_id, "canonical_name": "host-1"},
        )
    )
    monkeypatch.setattr("infralink.operator_surface.load_registry", lambda request: sources)
    monkeypatch.setattr(
        "infralink.cli.operations.resolve_apply_request", lambda root, host: object()
    )
    monkeypatch.setattr("infralink.cli.operations.operation_provider", lambda: provider)

    result = operation_status_operation(
        OperationStatusRequest(registry=tmp_path, operation_id=operation_id)
    )

    assert result.operation.id == operation_id
    assert result.operation.state == "converged"
    assert result.target == DoctorTarget(type="host", id=host_id, canonical_name="host-1")

    async def invoke_mcp() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            response = await client.call_tool(
                "operation.status",
                {"registry": str(tmp_path), "operation_id": operation_id},
            )
        assert response.is_error is False
        return response.structured_content

    payload = asyncio.run(invoke_mcp())
    assert payload["result"]["operation"] == {"id": operation_id, "state": "converged"}


def test_operation_status_is_discoverable_through_the_typed_mcp_adapter() -> None:
    async def list_tools() -> dict[str, object]:
        async with Client(operator_mcp_adapter().server) as client:
            tools = await client.list_tools()
        return {tool.name: tool.input_schema for tool in tools.tools}

    schemas = asyncio.run(list_tools())
    schema = schemas["operation.status"]
    assert isinstance(schema, dict)
    assert schema["type"] == "object"
    assert schema["required"] == ["operation_id"]
    properties = schema["properties"]
    assert isinstance(properties, dict)
    assert properties["operation_id"]["type"] == "string"
    assert properties["operation_id"]["cli"] == {"kind": "argument"}
    assert {branch.get("format") for branch in properties["registry"]["anyOf"]} == {
        "path",
        None,
    }
    assert {branch.get("format") for branch in properties["edges"]["anyOf"]} == {"path", None}


def test_observation_discovery_operations_preserve_the_typed_diagnostic_contract() -> None:
    capability = asyncio.run(observation_surface.invoke("capabilities", {}))
    assert capability.projections == ["observation", "secrets", "view", "readiness"]
    explanation = explain_operation(ExplainRequest(error_code="schema-version-missing"))
    assert explanation.code == "schema-version-missing"

    with pytest.raises(OperationError) as captured:
        explain_operation(ExplainRequest(error_code="not-a-real-code"))
    assert captured.value.code == "diagnostic-code-not-found"
