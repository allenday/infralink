from __future__ import annotations

import json
import subprocess
from base64 import b64encode
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from infralink.cli.contracts import (
    HostBootstrapAction,
    HostBootstrapRequest,
    HostControllerBootstrapSecretRef,
    HostControllerBootstrapState,
    HostReadinessCheck,
    HostReadinessResult,
)
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.host_readiness import evaluate_host_readiness as evaluate_readiness
from infralink.cli.main import (
    Context,
    _apply_bootstrap_request,
    _bootstrap_apply_request,
    _bootstrap_executor_actions,
    _bootstrap_executor_source,
    _bootstrap_failure_details,
    _bootstrap_plan_actions,
    _bootstrap_tailnet_address,
    _controller_bootstrap_state,
    _readiness_with_bws_token_required,
    _require_remote_tailnet_identity,
    _validate_bootstrap_bws_access,
    cli,
)
from infralink.host_readiness import HostReadinessProbe
from infralink.operator_operations.host_bootstrap import _bootstrap_pinned_transport

ROOT = Path(__file__).resolve().parents[1]
HOST_ID = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
HOST_NAME = "database.example.com"
HOST_FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


def test_bootstrap_failure_details_exposes_sanitized_failed_task_evidence() -> None:
    """A baseline failure names its task without leaking the BWS handoff token."""
    token = "0.11111111-1111-4111-8111-111111111111.secret-value"
    completed = subprocess.CompletedProcess(
        args=["ansible-playbook"],
        returncode=2,
        stdout=(
            "TASK [Bootstrap the controller-owned host runtime] *******************\n"
            "task path: /app/ansible/tasks/infralink_host_baseline.yml:96\n"
            'fatal: [100.91.194.110]: FAILED! => {"censored": "the output has been hidden"}\n'
        ),
        stderr=f"[WARNING]: BWS_ACCESS_TOKEN={token} was provided by the environment\n",
    )

    details = _bootstrap_failure_details(HOST_ID, completed, token=token)

    assert details == {
        "host": HOST_ID,
        "executor": "host_baseline",
        "return_code": 2,
        "task_count": 1,
        "failed_task": {
            "name": "Bootstrap the controller-owned host runtime",
            "path": "ansible/tasks/infralink_host_baseline.yml:96",
        },
        "stderr": "[WARNING]: BWS_ACCESS_TOKEN=[REDACTED] was provided by the environment",
    }
    assert token not in repr(details)


def test_bootstrap_failure_details_exposes_sanitized_nested_controller_failure() -> None:
    """The baseline executor returns bounded nested-controller evidence safely."""
    token = "0.11111111-1111-4111-8111-111111111111.secret-value"
    nested_failure = json.dumps(
        {
            "return_code": "2",
            "stdout_tail": "registry fetch failed",
            "stderr_tail": f"BWS_ACCESS_TOKEN={token}",
        },
        separators=(",", ":"),
    )
    completed = subprocess.CompletedProcess(
        args=["ansible-playbook"],
        returncode=2,
        stdout=(
            "TASK [Report sanitized controller bootstrap failure] *******************\n"
            "INFRALINK_BOOTSTRAP_NESTED_FAILURE_B64="
            f"{b64encode(nested_failure.encode()).decode()}\n"
        ),
        stderr="",
    )

    details = _bootstrap_failure_details(HOST_ID, completed, token=token)

    assert details["nested_failure"] == {
        "return_code": 2,
        "stdout_tail": "registry fetch failed",
        "stderr_tail": "BWS_ACCESS_TOKEN=[REDACTED]",
    }
    assert token not in repr(details)


def test_bootstrap_requires_a_manifest_ssh_fingerprint_not_a_legacy_operations_contract(
    tmp_path: Path,
) -> None:
    """A new host must get a precise bootstrap prerequisite, not a phantom contract path."""
    hosts = tmp_path / HOST_ID
    hosts.mkdir()
    (hosts / "manifest.yml").write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    tailscale_ip: 100.64.0.1\n"
        "    controller_bootstrap: {}\n",
        encoding="utf-8",
    )
    context = type("Context", (), {"hosts_path": tmp_path})()
    target = type(
        "Target",
        (),
        {"uuid": HOST_ID, "canonical_name": HOST_NAME, "tailscale_ip": "100.64.0.1"},
    )()

    with pytest.raises(CliFailure) as raised:
        with _bootstrap_pinned_transport(context, target, "100.64.0.1"):
            pass

    failure = raised.value
    assert failure.code is ErrorCode.CONFIGURATION_REQUIRED
    assert failure.message == "Bootstrap requires ssh.host_key_fingerprint"
    assert failure.details == {"host": HOST_ID}


def test_bootstrap_executor_uses_image_local_source_without_git(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The baked executor must not depend on a controller Git checkout."""
    executor_root = tmp_path / "app"
    manifest = executor_root / "ansible/executors/infralink-host-baseline.json"
    playbook = executor_root / "ansible/playbooks/infralink_host_baseline.yml"
    manifest.parent.mkdir(parents=True)
    playbook.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "infralink.host-bootstrap-executor/v1",
                "id": "infra-management-host-baseline",
                "playbook": "ansible/playbooks/infralink_host_baseline.yml",
                "allowed_actions": ["bootstrap_infralink_controller"],
            }
        ),
        encoding="utf-8",
    )
    playbook.write_text("---\n- hosts: all\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fail_on_subprocess(args: list[str], **_kwargs: object) -> None:
        commands.append(args)
        raise AssertionError(f"bootstrap executor must not run subprocesses: {args}")

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._BOOTSTRAP_EXECUTOR_ROOT", executor_root
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", fail_on_subprocess
    )

    with _bootstrap_executor_source(["bootstrap_infralink_controller"]) as (source, selected):
        assert source == executor_root
        assert selected == playbook

    assert commands == []


def test_canonical_controller_manifest_drives_verifier_and_dry_apply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The public apply path resolves one controller-bootstrap declaration."""
    manifest = tmp_path / "hosts" / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    status: active\n"
        "    tailscale_ip: 100.64.68.83\n"
        "    ssh:\n"
        f"      host_key_fingerprint: ssh-ed25519 {HOST_FINGERPRINT}\n"
        "    controller_bootstrap:\n"
        "      controller_image: ghcr.io/example/controller:main\n"
        "      registry_read_identity_secret:\n"
        "        project: fleet\n"
        "        id: 11111111-1111-4111-8111-111111111111\n"
        "      ghcr_auth:\n"
        "        username_secret:\n"
        "          project: fleet\n"
        "          id: 22222222-2222-4222-8222-222222222222\n"
        "        token_secret:\n"
        "          project: fleet\n"
        "          id: 33333333-3333-4333-8333-433333333333\n"
        "      registry_repo_url: ssh://git@example.invalid:2222/registry.git\n"
        "      registry_ref: main\n"
        "      registry_known_hosts: git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/\n",
        encoding="utf-8",
    )
    for args in (
        ["git", "init", "--quiet", str(tmp_path)],
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"],
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        ["git", "-C", str(tmp_path), "add", "."],
        ["git", "-C", str(tmp_path), "commit", "--quiet", "-m", "canonical registry"],
    ):
        subprocess.run(args, check=True, capture_output=True, text=True)
    monkeypatch.setattr(
        "infralink.cli.operations.validate_target_ssh_identity", lambda _request: None
    )

    verifier = CliRunner().invoke(
        cli,
        ["host", "verifier", HOST_ID, "--registry", str(tmp_path)],
    )
    dry_apply = CliRunner().invoke(
        cli,
        ["host", "apply", HOST_ID, "--registry", str(tmp_path), "--dry-run"],
    )

    assert verifier.exit_code == 1, verifier.output
    assert dry_apply.exit_code == 0, dry_apply.output
    verifier_payload = yaml.safe_load(verifier.output)
    apply_payload = yaml.safe_load(dry_apply.output)
    assert verifier_payload["result"]["target"]["id"] == HOST_ID
    assert verifier_payload["result"]["verifier"]["unavailable"]
    assert apply_payload["result"]["target"]["id"] == HOST_ID
    assert apply_payload["result"]["ssh_host_identity"] == "passed"


def test_doctor_reads_canonical_host_observation_inputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Doctor consumes one checkout and its declared observation bindings."""
    manifest = tmp_path / "hosts" / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    status: active\n"
        "    tailscale_ip: 100.64.68.83\n",
        encoding="utf-8",
    )
    deployment = manifest.parent / "operations" / "deployment.yml"
    deployment.parent.mkdir()
    deployment.write_text(
        "schema_version: self-deploy.desired-state.v1\n"
        "machine:\n"
        f"  uuid: {HOST_ID}\n"
        "controller:\n"
        "  image:\n"
        "    repository: ghcr.io/example/controller\n"
        "    tag: main\n"
        "compose:\n"
        "  project_name: infralink\n"
        "services:\n"
        "  protected: [infralink-controller]\n",
        encoding="utf-8",
    )
    for args in (
        ["git", "init", "--quiet", str(tmp_path)],
        ["git", "-C", str(tmp_path), "config", "user.email", "test@example.invalid"],
        ["git", "-C", str(tmp_path), "config", "user.name", "Test"],
        ["git", "-C", str(tmp_path), "add", "."],
        ["git", "-C", str(tmp_path), "commit", "--quiet", "-m", "canonical registry"],
    ):
        subprocess.run(args, check=True, capture_output=True, text=True)
    edges = tmp_path / "network/main/edges.yml"
    edges.parent.mkdir(parents=True)
    edges.write_text("edges: []\n", encoding="utf-8")
    observation_plan = tmp_path / "observation-plan.json"
    observation_plan.write_text(
        json.dumps(
            {
                "schema_version": "infralink.observation-plan/v1",
                "dependencies": [
                    {
                        "id": "host-ready",
                        "source_service_id": f"{HOST_ID}/host",
                        "target_service_id": f"{HOST_ID}/host",
                        "target_endpoint_id": f"{HOST_ID}/host/health",
                        "required": True,
                        "execution_adapter": "gatus",
                        "health_signal_refs": ["dependency/host-ready/health/reachable"],
                    }
                ],
                "service_profiles": [],
                "services": [],
            }
        ),
        encoding="utf-8",
    )
    bindings = tmp_path / "adapter-bindings.yml"
    bindings.write_text(
        "schema_version: infra-observe.adapter-bindings.v2\n"
        "bindings:\n"
        "  - id: host-ready\n"
        "    renderer_kind: gatus\n"
        "    observation_backend_id: core-health\n"
        "    output_identity: host-ready\n"
        "    result_identity: host-ready\n"
        "    signal_ref: dependency/host-ready/health/reachable\n",
        encoding="utf-8",
    )
    probe = HostReadinessProbe(
        reachable=True,
        hostname=HOST_NAME,
        machine_id="fixture-machine-id",
        commands={"git": True, "docker": True, "tailscale": True, "jq": True, "bws": True},
        devops_account=True,
        devops_authorized_access=True,
        bws_config=True,
        self_deploy_dependencies=True,
        self_deploy_runtime=True,
        self_deploy_timer_enabled=True,
        self_deploy_timer_active=True,
        self_deploy_mode="v2_reconcile",
        registry_layout="v2_managed",
        requires_v2_registry_layout=True,
        self_deploy_reconcile_result="success",
        self_deploy_reconcile_exit_status=0,
        self_deploy_reconcile_active_state="inactive",
        self_deploy_reconcile_sub_state="dead",
        self_deploy_reconcile_exit_timestamp_monotonic=1,
        controller_image="ghcr.io/example/controller:main",
        controller_python_version="3.12.3",
        error=None,
    )
    monkeypatch.setattr(
        "infralink.cli.doctor.SshReadinessTransport.probe", lambda _self, _address: probe
    )
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda _url, _token: [
            {
                "key": "host-ready",
                "results": [{"success": True, "timestamp": "2026-09-04T00:00:00Z"}],
            }
        ],
    )

    result = CliRunner().invoke(
        cli,
        [
            "doctor",
            "--registry",
            str(tmp_path),
            "--edges",
            str(edges),
            "--observation-plan",
            str(observation_plan),
            "--adapter-bindings",
            str(bindings),
            "--gatus-url",
            "http://gatus.test",
            "host",
            HOST_ID,
        ],
    )

    assert result.exit_code == 1, result.output
    payload = yaml.safe_load(result.output)
    assert payload["result"]["target"]["id"] == HOST_ID
    assert payload["result"]["evidence_summary"][0]["adapter"] == "gatus"
    assert payload["result"]["reason"] == "desired_state_contract_incomplete"


def test_host_bootstrap_rejects_missing_secure_connection_inputs_before_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bootstrap has no implicit transport or credential source."""
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.evaluate_host_readiness",
        lambda *_args: pytest.fail("bootstrap must validate inputs before SSH probing"),
    )

    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(ROOT / "examples" / "registry.yml"),
            "host",
            "bootstrap",
            HOST_ID,
        ],
    )

    assert result.exit_code == 2


def test_host_bootstrap_apply_requires_stdin_token_before_any_probe(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._bootstrap_pinned_transport",
        lambda *_args: pytest.fail("apply without a token must not start SSH"),
    )

    registry = tmp_path
    manifest = registry / "hosts" / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {HOST_ID}:\n    canonical_name: {HOST_NAME}\n    tailscale_ip: 100.64.68.83\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        [
            "host",
            "bootstrap",
            HOST_ID,
            "--registry",
            str(registry),
            "--format",
            "json",
            "--ssh-host",
            "100.64.68.83",
            "--apply",
        ],
    )

    assert result.exit_code == 3
    payload = json.loads(result.output)
    assert payload["error"]["details"] == {
        "host": HOST_ID,
        "requirement": "bws_token_stdin",
    }


def test_bootstrap_rejects_non_tailnet_or_mismatched_ssh_host_before_probe() -> None:
    target = type("Target", (), {"uuid": HOST_ID, "tailscale_ip": "100.64.68.83"})()

    with pytest.raises(CliFailure) as non_tailnet:
        _bootstrap_tailnet_address(target, "192.0.2.10")
    assert non_tailnet.value.code is ErrorCode.CONFIGURATION_REQUIRED

    with pytest.raises(CliFailure) as mismatch:
        _bootstrap_tailnet_address(target, "100.64.68.84")
    assert mismatch.value.details == {
        "host": HOST_ID,
        "declared_tailscale_ip": "100.64.68.83",
    }


def test_bootstrap_dry_plan_marks_a_missing_token_as_required() -> None:
    readiness = HostReadinessResult(
        transport="root_ssh",
        ready=True,
        checks=[],
        actions=[],
    )

    planned = _readiness_with_bws_token_required(readiness)

    assert not planned.ready
    assert planned.checks[-1].detail == "bws_token_required"
    assert planned.actions[-1].id == "provide_bws_token"


def test_bootstrap_cli_plan_advertises_apply_for_blank_host_executor_prerequisites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = tmp_path
    manifest = registry / "hosts" / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    tailscale_ip: 100.64.68.83\n"
        "    bws_machine_account: host-machine\n"
        "    bws_projects: [fleet]\n",
        encoding="utf-8",
    )

    @contextmanager
    def transport(*_args: object):
        yield type("Transport", (), {"probe": lambda _self, _address: object()})()

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._bootstrap_pinned_transport", transport
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._require_remote_tailnet_identity",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._controller_bootstrap_state",
        lambda *_args: HostControllerBootstrapState.model_validate(
            {
                "controller_image": "ghcr.io/example/controller:main",
                "registry_read_identity_secret": {
                    "project": "fleet",
                    "id": "11111111-1111-4111-8111-111111111111",
                },
                "registry_repo_url": "https://example.invalid/registry.git",
                "registry_ref": "main",
                "registry_known_hosts": "git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/",
            }
        ),
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.evaluate_host_readiness",
        lambda *_args, **_kwargs: HostReadinessResult(
            transport="root_ssh",
            ready=False,
            checks=[
                HostReadinessCheck(
                    id="machine_id",
                    required=True,
                    passed=False,
                    description="Machine UUID is missing.",
                ),
                HostReadinessCheck(
                    id="docker",
                    required=True,
                    passed=False,
                    description="Docker is missing.",
                ),
                HostReadinessCheck(
                    id="bws_config",
                    required=True,
                    passed=False,
                    description="BWS configuration is missing.",
                ),
                HostReadinessCheck(
                    id="self_deploy_runtime",
                    required=True,
                    passed=False,
                    description="Controller runtime is missing.",
                ),
                HostReadinessCheck(
                    id="self_deploy_timer",
                    required=True,
                    passed=False,
                    description="Controller timer is inactive.",
                ),
                HostReadinessCheck(
                    id="self_deploy_reconcile",
                    required=True,
                    passed=False,
                    description="Controller reconcile is unavailable.",
                ),
            ],
            actions=[
                HostBootstrapAction(
                    id="initialize_machine_id",
                    check_id="machine_id",
                    description="Initialize machine UUID.",
                ),
                HostBootstrapAction(
                    id="install_docker",
                    check_id="docker",
                    description="Install Docker.",
                ),
                HostBootstrapAction(
                    id="configure_bws",
                    check_id="bws_config",
                    description="Configure BWS.",
                ),
                HostBootstrapAction(
                    id="install_self_deploy_runtime",
                    check_id="self_deploy_runtime",
                    description="Install controller runtime.",
                ),
                HostBootstrapAction(
                    id="enable_self_deploy_timer",
                    check_id="self_deploy_timer",
                    description="Enable controller timer.",
                ),
                HostBootstrapAction(
                    id="inspect_self_deploy_reconcile",
                    check_id="self_deploy_reconcile",
                    description="Inspect controller reconcile.",
                ),
            ],
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["host", "bootstrap", HOST_ID, "--registry", str(registry), "--ssh-host", "100.64.68.83"],
    )

    assert result.exit_code == 1
    payload = yaml.safe_load(result.output)
    apply = next(item for item in payload["next_actions"] if item["rel"] == "apply")
    assert apply["command"] == (
        f"infralink host bootstrap {HOST_ID} --ssh-host 100.64.68.83 "
        f"--bws-token-stdin --apply --registry {registry}"
    )
    assert apply["safe"] is False


def test_bootstrap_plan_advertises_apply_handoff_for_declared_executor_prerequisites(
    tmp_path: Path,
) -> None:
    context = Context()
    context.registry_path = tmp_path / "hosts"
    target = type(
        "Target",
        (),
        {"uuid": HOST_ID, "canonical_name": HOST_NAME},
    )()
    readiness = _readiness_with_bws_token_required(
        HostReadinessResult(
            transport="root_ssh",
            ready=False,
            checks=[
                HostReadinessCheck(
                    id="docker",
                    required=True,
                    passed=False,
                    description="Docker is missing.",
                )
            ],
            actions=[
                HostBootstrapAction(
                    id="install_docker",
                    check_id="docker",
                    description="Install Docker.",
                )
            ],
        )
    )

    actions = _bootstrap_plan_actions(
        context,
        target,
        "100.64.68.83",
        readiness,
        bws_token_supplied=False,
    )

    assert [item.rel for item in actions] == ["reinspect-readiness", "apply"]


def test_bootstrap_plan_does_not_gate_initial_controller_image_materialization(
    tmp_path: Path,
) -> None:
    context = Context()
    context.registry_path = tmp_path / "hosts"
    target = type(
        "Target",
        (),
        {"uuid": HOST_ID, "canonical_name": HOST_NAME},
    )()
    readiness = _readiness_with_bws_token_required(
        HostReadinessResult(
            transport="root_ssh",
            ready=False,
            checks=[
                HostReadinessCheck(
                    id="controller_python",
                    required=True,
                    passed=False,
                    description="The controller image has not been materialized yet.",
                    detail="controller_image_unresolved",
                )
            ],
            actions=[
                HostBootstrapAction(
                    id="refresh_controller_image",
                    check_id="controller_python",
                    description="Refresh the controller image.",
                )
            ],
        )
    )

    actions = _bootstrap_plan_actions(
        context,
        target,
        "100.64.68.83",
        readiness,
        bws_token_supplied=False,
    )

    assert [item.rel for item in actions] == ["reinspect-readiness", "apply"]


def test_bootstrap_plan_omits_apply_handoff_for_manual_ssh_prerequisite(
    tmp_path: Path,
) -> None:
    context = Context()
    context.registry_path = tmp_path / "hosts"
    target = type(
        "Target",
        (),
        {"uuid": HOST_ID, "canonical_name": HOST_NAME},
    )()
    readiness = _readiness_with_bws_token_required(
        HostReadinessResult(
            transport="root_ssh",
            ready=False,
            checks=[
                HostReadinessCheck(
                    id="ssh",
                    required=True,
                    passed=False,
                    description="Root SSH is unavailable.",
                )
            ],
            actions=[
                HostBootstrapAction(
                    id="establish_root_ssh",
                    check_id="ssh",
                    description="Establish root SSH.",
                )
            ],
        )
    )

    actions = _bootstrap_plan_actions(
        context,
        target,
        "100.64.68.83",
        readiness,
        bws_token_supplied=False,
    )

    assert [item.rel for item in actions] == ["reinspect-readiness"]


def test_bootstrap_executor_carries_missing_prerequisites_and_one_controller_action() -> None:
    readiness = HostReadinessResult(
        transport="root_ssh",
        ready=False,
        checks=[],
        actions=[
            HostBootstrapAction(
                id="initialize_machine_id",
                check_id="machine_id",
                description="Machine UUID.",
            ),
            HostBootstrapAction(id="install_git", check_id="git", description="Git."),
            HostBootstrapAction(id="install_docker", check_id="docker", description="Docker."),
            HostBootstrapAction(id="install_jq", check_id="jq", description="jq."),
            HostBootstrapAction(id="install_bws_cli", check_id="bws", description="BWS."),
            HostBootstrapAction(
                id="configure_bws",
                check_id="bws_config",
                description="Configure BWS.",
            ),
            HostBootstrapAction(
                id="install_self_deploy_runtime",
                check_id="self_deploy_runtime",
                description="Install controller runtime.",
            ),
            HostBootstrapAction(
                id="enable_self_deploy_timer",
                check_id="self_deploy_timer",
                description="Enable controller timer.",
            ),
            HostBootstrapAction(
                id="inspect_self_deploy_reconcile",
                check_id="self_deploy_reconcile",
                description="Inspect controller reconcile.",
            ),
            HostBootstrapAction(
                id="create_devops_account", check_id="devops", description="obsolete"
            ),
        ],
    )
    assert _bootstrap_executor_actions(readiness) == [
        "install_git",
        "install_docker",
        "install_jq",
        "install_bws_cli",
        "bootstrap_infralink_controller",
    ]
    target = type(
        "Target",
        (),
        {
            "uuid": HOST_ID,
            "canonical_name": HOST_NAME,
            "tailscale_ip": "100.64.68.83",
        },
    )()
    request = _bootstrap_apply_request(
        Context(),
        target,
        _bootstrap_executor_actions(readiness),
        controller_state=HostControllerBootstrapState.model_validate(
            {
                "controller_image": "ghcr.io/example/controller:main",
                "registry_read_identity_secret": {
                    "project": "fleet",
                    "id": "11111111-1111-4111-8111-111111111111",
                },
                "registry_repo_url": "https://example.invalid/registry.git",
                "registry_ref": "main",
                "registry_known_hosts": "git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/",
            }
        ),
    )
    assert "initialize_machine_id" not in request.bootstrap_actions
    assert "configure_bws" not in request.bootstrap_actions
    assert "install_self_deploy_runtime" not in request.bootstrap_actions
    assert "enable_self_deploy_timer" not in request.bootstrap_actions
    assert "inspect_self_deploy_reconcile" not in request.bootstrap_actions


def test_controller_bootstrap_requires_a_registry_with_a_structured_remediation() -> None:
    target = type("Target", (), {"uuid": HOST_ID})()

    with pytest.raises(CliFailure) as raised:
        _controller_bootstrap_state(None, target)

    assert raised.value.code is ErrorCode.CONFIGURATION_REQUIRED
    assert raised.value.fix == (
        "Provide the registry checkout root with --registry and rerun host bootstrap"
    )


def test_controller_bootstrap_requires_declared_ghcr_auth(tmp_path: Path) -> None:
    registry = tmp_path
    manifest = registry / "hosts" / HOST_ID / "manifest.yml"
    deployment = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        "    controller_bootstrap:\n"
        "      registry_read_identity_secret:\n"
        "        project: fleet\n"
        "        id: 11111111-1111-4111-8111-111111111111\n"
        "      registry_repo_url: ssh://git@example.invalid:2222/registry.git\n"
        "      registry_ref: main\n"
        "      registry_known_hosts: git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/\n",
        encoding="utf-8",
    )
    deployment.write_text(
        "controller:\n  image:\n    repository: ghcr.io/example/controller\n    tag: main\n",
        encoding="utf-8",
    )

    with pytest.raises(CliFailure) as raised:
        _controller_bootstrap_state(registry / "hosts", type("Target", (), {"uuid": HOST_ID})())

    assert raised.value.code is ErrorCode.CONFIGURATION_REQUIRED
    assert raised.value.details["missing"] == ["controller_bootstrap.ghcr_auth"]


def test_controller_bootstrap_projects_ghcr_auth_references() -> None:
    state = HostControllerBootstrapState.model_validate(
        {
            "controller_image": "ghcr.io/example/controller:main",
            "registry_read_identity_secret": {
                "project": "fleet",
                "id": "11111111-1111-4111-8111-111111111111",
            },
            "ghcr_auth": {
                "username_secret": {
                    "project": "fleet",
                    "id": "22222222-2222-4222-8222-222222222222",
                },
                "token_secret": {
                    "project": "fleet",
                    "id": "33333333-3333-4333-8333-333333333333",
                },
            },
            "registry_repo_url": "https://example.invalid/registry.git",
            "registry_ref": "main",
            "registry_known_hosts": "git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/",
        }
    )
    request = HostBootstrapRequest.model_validate(
        {
            "host_address": "100.64.68.83",
            "host_uuid": HOST_ID,
            "canonical_name": HOST_NAME,
            "bootstrap_actions": ["bootstrap_infralink_controller"],
            "controller_bootstrap": state,
        }
    )

    assert request.ansible_extra_vars()["ghcr_username_secret_uuid"] == (
        "22222222-2222-4222-8222-222222222222"
    )
    assert request.ansible_extra_vars()["ghcr_token_secret_project"] == "fleet"


def test_controller_bootstrap_state_reads_the_canonical_declaration(tmp_path: Path) -> None:
    registry = tmp_path / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
    deployment = registry / HOST_ID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        "    controller_bootstrap:\n"
        "      registry_read_identity_secret:\n"
        "        project: fleet\n"
        "        id: 11111111-1111-4111-8111-111111111111\n"
        "      ghcr_auth:\n"
        "        username_secret:\n"
        "          project: fleet\n"
        "          id: 22222222-2222-4222-8222-222222222222\n"
        "        token_secret:\n"
        "          project: fleet\n"
        "          id: 33333333-3333-4333-8333-433333333333\n"
        "      registry_repo_url: ssh://git@example.invalid:2222/registry.git\n"
        "      registry_ref: main\n"
        "      registry_known_hosts: git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/\n",
        encoding="utf-8",
    )
    deployment.write_text(
        "controller:\n  image:\n    repository: ghcr.io/example/controller\n    tag: main\n",
        encoding="utf-8",
    )

    state = _controller_bootstrap_state(registry, type("Target", (), {"uuid": HOST_ID})())

    assert state.controller_image == "ghcr.io/example/controller:main"
    assert state.registry_ref == "main"


def test_bootstrap_apply_runs_the_baked_executor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    executor_root = tmp_path / "app"
    manifest = executor_root / "ansible/executors/infralink-host-baseline.json"
    playbook = executor_root / "ansible/playbooks/infralink_host_baseline.yml"
    manifest.parent.mkdir(parents=True)
    playbook.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "infralink.host-bootstrap-executor/v1",
                "id": "infra-management-host-baseline",
                "playbook": "ansible/playbooks/infralink_host_baseline.yml",
                "allowed_actions": ["bootstrap_infralink_controller"],
            }
        ),
        encoding="utf-8",
    )
    playbook.write_text("---\n- hosts: all\n", encoding="utf-8")
    commands: list[list[str]] = []
    state = HostControllerBootstrapState.model_validate(
        {
            "controller_image": "ghcr.io/example/controller:main",
            "registry_read_identity_secret": {
                "project": "fleet",
                "id": "11111111-1111-4111-8111-111111111111",
            },
            "ghcr_auth": {
                "username_secret": {
                    "project": "fleet",
                    "id": "22222222-2222-4222-8222-222222222222",
                },
                "token_secret": {
                    "project": "fleet",
                    "id": "33333333-3333-4333-8333-433333333333",
                },
            },
            "registry_repo_url": "ssh://git@example.invalid:2222/registry.git",
            "registry_ref": "main",
            "registry_known_hosts": "git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/",
        }
    )
    target = type(
        "Target",
        (),
        {
            "uuid": HOST_ID,
            "canonical_name": HOST_NAME,
            "tailscale_ip": "100.64.68.83",
        },
    )()
    readiness = HostReadinessResult(transport="root_ssh", ready=True, checks=[], actions=[])

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._BOOTSTRAP_EXECUTOR_ROOT", executor_root
    )
    monkeypatch.setattr("infralink.operator_operations.host_bootstrap.subprocess.run", fake_run)
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.evaluate_host_readiness",
        lambda *_args, **_kwargs: readiness,
    )

    result = _apply_bootstrap_request(
        Context(),
        target,
        "100.64.68.83",
        ["bootstrap_infralink_controller"],
        state,
        None,
        tmp_path / "known_hosts",
    )

    assert result.ready is True
    assert commands[0][0:2] == ["ansible-playbook", "-vv"]
    assert str(playbook) in commands[0]


def test_bootstrap_request_projects_only_declared_controller_state() -> None:
    """Baseline actions do not revive the retired V2 promotion contract."""
    request = HostBootstrapRequest.model_validate(
        {
            "host_address": "100.64.68.83",
            "host_uuid": HOST_ID,
            "canonical_name": HOST_NAME,
            "bootstrap_actions": [
                "migrate_v2_registry_layout",
                "install_self_deploy_runtime",
                "bootstrap_infralink_controller",
            ],
            "controller_bootstrap": {
                "controller_image": "ghcr.io/example/controller:main",
                "registry_read_identity_secret": {
                    "project": "fleet",
                    "id": "11111111-1111-4111-8111-111111111111",
                },
                "ghcr_auth": {
                    "username_secret": {
                        "project": "fleet",
                        "id": "22222222-2222-4222-8222-222222222222",
                    },
                    "token_secret": {
                        "project": "fleet",
                        "id": "33333333-3333-4333-8333-333333333333",
                    },
                },
                "registry_repo_url": "https://example.invalid/registry.git",
                "registry_ref": "main",
                "registry_known_hosts": "git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/",
            },
        }
    )

    assert not any(
        key.startswith("self_deploy_v2_promotion") for key in request.ansible_extra_vars()
    )


def test_controller_bootstrap_rejects_reusing_registry_identity_as_ghcr_credential() -> None:
    with pytest.raises(ValidationError, match="must not reuse"):
        HostControllerBootstrapState.model_validate(
            {
                "controller_image": "ghcr.io/example/controller:main",
                "registry_read_identity_secret": {
                    "project": "fleet",
                    "id": "11111111-1111-4111-8111-111111111111",
                },
                "ghcr_auth": {
                    "username_secret": {
                        "project": "fleet",
                        "id": "11111111-1111-4111-8111-111111111111",
                    },
                    "token_secret": {
                        "project": "fleet",
                        "id": "33333333-3333-4333-8333-333333333333",
                    },
                },
                "registry_repo_url": "https://example.invalid/registry.git",
                "registry_ref": "main",
                "registry_known_hosts": "git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/",
            }
        )


def test_controller_bootstrap_requires_declared_registry_known_hosts(tmp_path: Path) -> None:
    registry = tmp_path
    manifest = registry / "hosts" / HOST_ID / "manifest.yml"
    deployment = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    controller_bootstrap:\n"
        "      registry_read_identity_secret:\n"
        "        project: fleet\n"
        "        id: 11111111-1111-4111-8111-111111111111\n"
        "      registry_repo_url: ssh://git@example.invalid:2222/registry.git\n"
        "      registry_ref: main\n",
        encoding="utf-8",
    )
    deployment.write_text(
        "controller:\n  image:\n    repository: ghcr.io/example/controller\n    tag: main\n",
        encoding="utf-8",
    )
    target = type("Target", (), {"uuid": HOST_ID})()

    with pytest.raises(CliFailure) as raised:
        _controller_bootstrap_state(registry, target)

    assert raised.value.code is ErrorCode.CONFIGURATION_REQUIRED
    assert raised.value.details["required_manifest_fields"][-1] == (
        "controller_bootstrap.registry_known_hosts"
    )


def test_controller_bootstrap_uses_invoking_seed_for_preservation_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A preserve-only declaration has no Compose controller image by design."""
    registry = tmp_path
    manifest = registry / "hosts" / HOST_ID / "manifest.yml"
    deployment = registry / "hosts" / HOST_ID / "operations" / "deployment.yml"
    deployment.parent.mkdir(parents=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        "    controller_bootstrap:\n"
        "      registry_read_identity_secret:\n"
        "        project: fleet\n"
        "        id: 11111111-1111-4111-8111-111111111111\n"
        "      ghcr_auth:\n"
        "        username_secret:\n"
        "          project: fleet\n"
        "          id: 22222222-2222-4222-8222-222222222222\n"
        "        token_secret:\n"
        "          project: fleet\n"
        "          id: 33333333-3333-4333-8333-333333333333\n"
        "      registry_repo_url: ssh://git@example.invalid:2222/registry.git\n"
        "      registry_ref: main\n"
        "      registry_known_hosts: git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/\n",
        encoding="utf-8",
    )
    deployment.write_text(
        "schema_version: self-deploy.preservation-state.v1\n"
        "mode: preserve-only\n"
        f"machine: {{uuid: {HOST_ID}, status: active}}\n"
        "infra_management:\n"
        "  provider: git\n"
        "  source: {remote: https://example.invalid/management.git, ref: refs/heads/main}\n"
        "  revision: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
        "working_tree: preserve-dirty\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("INFRALINK_CONTROLLER_IMAGE", "ghcr.io/example/controller:main")

    state = _controller_bootstrap_state(registry / "hosts", type("Target", (), {"uuid": HOST_ID})())

    assert state.controller_image == "ghcr.io/example/controller:main"
    assert state.registry_ref == "main"


@pytest.mark.parametrize("known_hosts", ["", "git.example.invalid ssh-ed25519 not-base64"])
def test_controller_bootstrap_rejects_invalid_registry_known_hosts(known_hosts: str) -> None:
    with pytest.raises(ValidationError, match="registry_known_hosts"):
        HostControllerBootstrapState.model_validate(
            {
                "controller_image": "ghcr.io/example/controller:main",
                "registry_read_identity_secret": {
                    "project": "fleet",
                    "id": "11111111-1111-4111-8111-111111111111",
                },
                "registry_repo_url": "ssh://git@example.invalid:2222/registry.git",
                "registry_ref": "main",
                "registry_known_hosts": known_hosts,
            }
        )


def test_bootstrap_reports_missing_controller_declaration_with_inspection_action(
    tmp_path: Path,
) -> None:
    registry = tmp_path
    manifest = registry / "hosts" / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    tailscale_ip: 100.64.68.83\n"
        "    bws_machine_account: host-machine\n"
        "    bws_projects: [fleet]\n",
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        ["host", "bootstrap", HOST_ID, "--registry", str(registry), "--ssh-host", "100.64.68.83"],
    )

    assert result.exit_code == 3
    payload = yaml.safe_load(result.output)
    assert payload["error"] == {
        "code": "configuration_required",
        "message": "Selected host declaration lacks canonical controller bootstrap state",
        "details": {
            "host": HOST_ID,
            "manifest_path": str(manifest),
            "deployment_path": str(registry / "hosts" / HOST_ID / "operations" / "deployment.yml"),
            "required_manifest_fields": [
                "controller_bootstrap.registry_read_identity_secret.project",
                "controller_bootstrap.registry_read_identity_secret.id",
                "controller_bootstrap.registry_repo_url",
                "controller_bootstrap.registry_ref",
                "controller_bootstrap.registry_known_hosts",
            ],
            "required_deployment_fields": [
                "controller.image.repository",
                "controller.image.tag",
                "controller.image.branch (when controller.image.tag is head)",
            ],
        },
    }
    assert payload["next_actions"] == [
        {
            "rel": "inspect",
            "command": f"infralink host show {HOST_ID} --registry {registry}",
            "description": "Inspect the target host declaration",
            "safe": True,
        }
    ]


def test_bootstrap_uses_baked_executor_without_a_control_checkout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bootstrap uses only the image-local executor."""

    executor_root = tmp_path / "app"
    manifest = executor_root / "ansible/executors/infralink-host-baseline.json"
    playbook = executor_root / "ansible/playbooks/infralink_host_baseline.yml"
    manifest.parent.mkdir(parents=True)
    playbook.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "infralink.host-bootstrap-executor/v1",
                "id": "infra-management-host-baseline",
                "playbook": "ansible/playbooks/infralink_host_baseline.yml",
                "runtime_mode": "controller_bootstrap",
                "required_inputs": [],
                "allowed_actions": ["bootstrap_infralink_controller"],
            }
        ),
        encoding="utf-8",
    )
    playbook.write_text("---\n- hosts: all\n", encoding="utf-8")
    commands: list[list[str]] = []
    ansible_cwds: list[Path] = []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if args[0] == "ansible-playbook":
            ansible_cwds.append(kwargs["cwd"])
            return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    target = type(
        "Target",
        (),
        {
            "uuid": HOST_ID,
            "canonical_name": HOST_NAME,
        },
    )()
    controller = HostControllerBootstrapState.model_validate(
        {
            "controller_image": "ghcr.io/example/controller:main",
            "registry_read_identity_secret": {
                "project": "fleet",
                "id": "11111111-1111-4111-8111-111111111111",
            },
            "registry_repo_url": "https://example.invalid/registry.git",
            "registry_ref": "main",
            "registry_known_hosts": "git.example.invalid ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIG8mjZa4jsBejgu0NWewMIfAw6C9tg1qpf0tFPipYz1/",
        }
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._BOOTSTRAP_EXECUTOR_ROOT", executor_root
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", recording_run
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.evaluate_host_readiness",
        lambda *_args, **_kwargs: HostReadinessResult(
            transport="root_ssh", ready=True, checks=[], actions=[]
        ),
    )

    _apply_bootstrap_request(
        Context(),
        target,
        "100.64.68.83",
        ["bootstrap_infralink_controller"],
        controller,
        "bws-token",
        tmp_path / "known_hosts",
    )

    assert any(command[0] == "ansible-playbook" for command in commands)
    assert ["ansible-playbook", "-vv"] in [command[:2] for command in commands]
    assert ansible_cwds == [executor_root]
    executor_vars = json.loads(
        next(command[-1] for command in commands if command[0] == "ansible-playbook")
    )
    assert executor_vars["registry_known_hosts"] == controller.registry_known_hosts
    assert not any(command[:2] == ["git", "-C"] for command in commands)


def test_bootstrap_rejects_remote_without_the_declared_tailnet_address() -> None:
    target = type("Target", (), {"uuid": HOST_ID})()
    probe = HostReadinessProbe(
        reachable=True,
        hostname=HOST_NAME,
        machine_id="machine-id",
        commands={},
        devops_account=False,
        devops_authorized_access=False,
        bws_config=False,
        self_deploy_runtime=False,
        self_deploy_timer_enabled=False,
        self_deploy_timer_active=False,
        error=None,
        tailscale_ips=("100.64.68.84",),
    )

    with pytest.raises(CliFailure) as mismatch:
        _require_remote_tailnet_identity(target, probe, "100.64.68.83")

    assert mismatch.value.code is ErrorCode.CONFIGURATION_REQUIRED
    assert mismatch.value.details["declared_tailscale_ip"] == "100.64.68.83"


def test_bootstrap_bws_validation_uses_only_environment_for_the_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = tmp_path / "hosts"
    catalog = registry.parent / "ansible/inventory/bws_projects.yml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "projects:\n  fleet:\n    uuid: 11111111-1111-4111-8111-111111111111\n",
        encoding="utf-8",
    )
    context = type("Context", (), {"registry_path": registry})()
    calls: list[tuple[list[str], dict[str, object]]] = []
    token = "bws-token-not-for-output"

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")

    monkeypatch.setattr("infralink.operator_operations.host_bootstrap.subprocess.run", fake_run)

    _validate_bootstrap_bws_access(context, ("fleet",), token)

    assert calls[0][0] == [
        "bws",
        "project",
        "get",
        "11111111-1111-4111-8111-111111111111",
        "--output",
        "none",
    ]
    assert token not in " ".join(calls[0][0])
    assert calls[0][1]["env"]["BWS_ACCESS_TOKEN"] == token


def test_bootstrap_bws_validation_checks_declared_ghcr_secret_references(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    registry = tmp_path / "hosts"
    catalog = registry.parent / "ansible/inventory/bws_projects.yml"
    catalog.parent.mkdir(parents=True)
    project_id = "11111111-1111-4111-8111-111111111111"
    catalog.write_text(f"projects:\n  fleet:\n    uuid: {project_id}\n", encoding="utf-8")
    context = type("Context", (), {"registry_path": registry})()
    token = "bws-token-not-for-output"
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        output = json.dumps({"projectId": project_id}) if args[1:3] == ["secret", "get"] else ""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=output, stderr="")

    monkeypatch.setattr("infralink.operator_operations.host_bootstrap.subprocess.run", fake_run)

    _validate_bootstrap_bws_access(
        context,
        ("fleet",),
        token,
        controller_secrets={
            "ghcr_username": HostControllerBootstrapSecretRef(
                project="fleet", id="22222222-2222-4222-8222-222222222222"
            ),
            "ghcr_token": HostControllerBootstrapSecretRef(
                project="fleet", id="33333333-3333-4333-8333-333333333333"
            ),
        },
    )

    assert [call[3] for call in calls if call[1:3] == ["secret", "get"]] == [
        "22222222-2222-4222-8222-222222222222",
        "33333333-3333-4333-8333-333333333333",
    ]
    assert token not in " ".join(" ".join(call) for call in calls)


def test_bootstrap_bws_validation_rejects_ghcr_secret_from_undeclared_project(
    tmp_path: Path,
) -> None:
    registry = tmp_path / "hosts"
    catalog = registry.parent / "ansible/inventory/bws_projects.yml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "projects:\n"
        "  fleet:\n    uuid: 11111111-1111-4111-8111-111111111111\n"
        "  elsewhere:\n    uuid: 22222222-2222-4222-8222-222222222222\n",
        encoding="utf-8",
    )
    context = type("Context", (), {"registry_path": registry})()

    with pytest.raises(CliFailure) as raised:
        _validate_bootstrap_bws_access(
            context,
            ("fleet",),
            "bws-token-not-for-output",
            controller_secrets={
                "ghcr_token": HostControllerBootstrapSecretRef(
                    project="elsewhere", id="33333333-3333-4333-8333-333333333333"
                )
            },
        )

    assert raised.value.code is ErrorCode.CONFIGURATION_REQUIRED
    assert raised.value.details["projects"] == ["elsewhere"]


def test_cli_readiness_enforces_declared_v2_registry_layout_migration() -> None:
    probe = HostReadinessProbe(
        reachable=True,
        hostname="database.example.com",
        machine_id="machine-id",
        commands={"git": True, "docker": True, "tailscale": True, "jq": True, "bws": True},
        devops_account=True,
        devops_authorized_access=True,
        bws_config=True,
        self_deploy_dependencies=True,
        self_deploy_runtime=True,
        self_deploy_timer_enabled=True,
        self_deploy_timer_active=True,
        error=None,
        self_deploy_mode="legacy_pull",
        registry_layout="legacy_nested",
    )
    host = type(
        "Host",
        (),
        {
            "canonical_name": "database.example.com",
            "tailscale_ip": "192.0.2.10",
            "public_ip": None,
            "self_deploy_v2_registry_layout_enabled": True,
        },
    )()
    transport = type("Transport", (), {"probe": lambda _self, _address: probe})()

    readiness = evaluate_readiness(host, transport)

    layout = next(check for check in readiness.checks if check.id == "registry_layout")
    assert readiness.requires_v2_registry_layout is True
    assert layout.passed is False
    assert layout.detail == "legacy_nested"
