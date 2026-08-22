from __future__ import annotations

import json
import os
import subprocess
import sys
from base64 import b64encode
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from pydantic import ValidationError

from infralink.cli.contracts import (
    HostBootstrapAction,
    HostControllerBootstrapState,
    HostReadinessCheck,
    HostReadinessResult,
)
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.host_readiness import evaluate_host_readiness as evaluate_readiness
from infralink.cli.main import (
    Context,
    _apply_bootstrap_request,
    _apply_controller_refresh,
    _bootstrap_apply_request,
    _bootstrap_executor_actions,
    _bootstrap_executor_source,
    _bootstrap_failure_details,
    _bootstrap_plan_actions,
    _bootstrap_tailnet_address,
    _controller_bootstrap_state,
    _controller_refresh_source,
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


def test_control_root_can_be_supplied_by_the_controller_runtime(
    tmp_path: Path,
) -> None:
    environment = {
        **os.environ,
        "INFRALINK_CONTROL_ROOT": str(tmp_path),
        "PYTHONPATH": str(ROOT / "src"),
    }

    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            "from infralink.operator_operations.host_bootstrap import _CONTROL_ROOT; print(_CONTROL_ROOT)",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=environment,
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == str(tmp_path)


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


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _controller_source_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "infra-management"
    playbook = repository / "ansible/playbooks/infralink_controller_refresh.yml"
    playbook.parent.mkdir(parents=True)
    playbook.write_text("---\n- hosts: all\n", encoding="utf-8")
    _git(repository.parent, "init", "--quiet", str(repository))
    _git(repository, "config", "user.email", "test@example.invalid")
    _git(repository, "config", "user.name", "Test")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "controller refresh")
    _git(repository, "branch", "-M", "main")
    return repository, _git(repository, "rev-parse", "HEAD")


def _controller_clone_missing_selected_revision(tmp_path: Path) -> tuple[Path, str, str]:
    source, revision = _controller_source_repository(tmp_path / "upstream")
    remote = tmp_path / "infra-management.git"
    _git(tmp_path, "clone", "--quiet", "--bare", str(source), str(remote))

    control = tmp_path / "control"
    _git(tmp_path, "init", "--quiet", str(control))
    _git(control, "config", "user.email", "test@example.invalid")
    _git(control, "config", "user.name", "Test")
    (control / "README").write_text("control checkout\n", encoding="utf-8")
    _git(control, "add", ".")
    _git(control, "commit", "--quiet", "-m", "control checkout")
    _git(control, "remote", "add", "origin", remote.as_uri())
    return control, remote.as_uri(), revision


def _controller_clone_with_selected_revision_missing_from_main(
    tmp_path: Path,
) -> tuple[Path, str, str]:
    source, _main_revision = _controller_source_repository(tmp_path / "upstream")
    remote = tmp_path / "infra-management.git"
    _git(tmp_path, "clone", "--quiet", "--bare", str(source), str(remote))

    (source / "selected-only").write_text("not advertised on remote main\n", encoding="utf-8")
    _git(source, "add", ".")
    _git(source, "commit", "--quiet", "-m", "selected only")
    selected_revision = _git(source, "rev-parse", "HEAD")

    control = tmp_path / "control"
    _git(tmp_path, "init", "--quiet", str(control))
    _git(control, "config", "user.email", "test@example.invalid")
    _git(control, "config", "user.name", "Test")
    (control / "README").write_text("control checkout\n", encoding="utf-8")
    _git(control, "add", ".")
    _git(control, "commit", "--quiet", "-m", "control checkout")
    _git(control, "remote", "add", "origin", remote.as_uri())
    _git(control, "fetch", "--quiet", str(source), selected_revision)
    return control, remote.as_uri(), selected_revision


def _configure_self_remote(monkeypatch: pytest.MonkeyPatch, repository: Path) -> str:
    remote = repository.as_uri()
    _git(repository, "remote", "add", "origin", remote)
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._CONTROLLER_REFRESH_SOURCE_REMOTE", remote
    )
    return remote


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

    registry = tmp_path / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        f"hosts:\n  {HOST_ID}:\n    canonical_name: {HOST_NAME}\n    tailscale_ip: 100.64.68.83\n",
        encoding="utf-8",
    )
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(registry),
            "host",
            "bootstrap",
            HOST_ID,
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
    registry = tmp_path / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
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
        ["--registry", str(registry), "host", "bootstrap", HOST_ID, "--ssh-host", "100.64.68.83"],
    )

    assert result.exit_code == 1
    payload = yaml.safe_load(result.output)
    apply = next(item for item in payload["next_actions"] if item["rel"] == "apply")
    assert apply["command"] == (
        "printf '%s\\n' \"$HOST_BWS_TOKEN\" | "
        f"infralink --registry {registry} host bootstrap {HOST_ID} "
        "--ssh-host 100.64.68.83 --bws-token-stdin --apply"
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


def test_controller_bootstrap_requires_declared_registry_known_hosts(tmp_path: Path) -> None:
    registry = tmp_path / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
    deployment = registry / HOST_ID / "operations" / "deployment.yml"
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
    registry = tmp_path / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
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
        ["--registry", str(registry), "host", "bootstrap", HOST_ID, "--ssh-host", "100.64.68.83"],
    )

    assert result.exit_code == 3
    payload = yaml.safe_load(result.output)
    assert payload["error"] == {
        "code": "configuration_required",
        "message": "Selected host declaration lacks canonical controller bootstrap state",
        "details": {
            "host": HOST_ID,
            "manifest_path": str(manifest),
            "deployment_path": str(registry / HOST_ID / "operations" / "deployment.yml"),
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
            "command": f"infralink --registry {registry} host show {HOST_ID}",
            "description": "Inspect the target host declaration",
            "safe": True,
        }
    ]


def test_bootstrap_uses_baked_executor_when_control_checkout_is_dirty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Bootstrap ignores the controller checkout and uses the image executor."""
    control, _revision = _controller_source_repository(tmp_path)
    (control / "dirty").write_text("must not select bootstrap code", encoding="utf-8")

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
    monkeypatch.setattr("infralink.operator_operations.host_bootstrap._CONTROL_ROOT", control)
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


def test_controller_refresh_materializes_an_exact_detached_source(
    monkeypatch, tmp_path: Path
) -> None:
    repository, revision = _controller_source_repository(tmp_path)
    _configure_self_remote(monkeypatch, repository)

    with _controller_refresh_source(repository, revision) as source:
        assert source != repository
        assert _git(source, "rev-parse", "HEAD") == revision
        assert (source / "ansible/playbooks/infralink_controller_refresh.yml").is_file()


def test_controller_refresh_rejects_a_dirty_or_missing_controller_source(
    monkeypatch, tmp_path: Path
) -> None:
    repository, revision = _controller_source_repository(tmp_path)
    _configure_self_remote(monkeypatch, repository)
    (repository / "dirty").write_text("not accepted", encoding="utf-8")

    with pytest.raises(CliFailure) as dirty:
        with _controller_refresh_source(repository, revision):
            pass
    assert dirty.value.code == ErrorCode.PROVIDER_UNAVAILABLE

    (repository / "dirty").unlink()
    with pytest.raises(CliFailure) as missing:
        with _controller_refresh_source(repository, "a" * 40):
            pass
    assert missing.value.code == ErrorCode.PROVIDER_UNAVAILABLE


def test_controller_refresh_fetches_main_to_materialize_the_absent_selected_revision(
    monkeypatch, tmp_path: Path
) -> None:
    control, remote, revision = _controller_clone_missing_selected_revision(tmp_path)
    unsafe_marker = tmp_path / "unsafe-helper-ran"
    _git(control, "config", "--local", "credential.helper", f"!touch {unsafe_marker}")
    commands: list[tuple[list[str], dict[str, object]]] = []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append((args, kwargs))
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._CONTROLLER_REFRESH_SOURCE_REMOTE", remote
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", recording_run
    )

    with _controller_refresh_source(control, revision) as source:
        assert _git(source, "rev-parse", "HEAD") == revision

    fetches = [args for args, _kwargs in commands if "fetch" in args]
    assert fetches == [
        [
            "git",
            "-C",
            str(control),
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        ]
    ]
    fetch_env = next(kwargs["env"] for args, kwargs in commands if "fetch" in args)
    assert isinstance(fetch_env, dict)
    assert fetch_env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert fetch_env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert fetch_env["HOME"] == "/root"
    assert fetch_env["GIT_CONFIG_COUNT"] == "3"
    assert fetch_env["GIT_CONFIG_KEY_0"] == "credential.helper"
    assert fetch_env["GIT_CONFIG_VALUE_0"] == ""
    assert fetch_env["GIT_CONFIG_KEY_1"] == "credential.helper"
    assert fetch_env["GIT_CONFIG_VALUE_1"] == "store"
    assert fetch_env["GIT_CONFIG_KEY_2"] == "credential.useHttpPath"
    assert fetch_env["GIT_CONFIG_VALUE_2"] == "true"

    credential_home = tmp_path / "credentials"
    credential_home.mkdir()
    (credential_home / ".git-credentials").write_text(
        "https://x-access-token:test-token@github.com/relax-dot-gg/infra-management.git\n",
        encoding="utf-8",
    )
    credential = subprocess.run(
        ["git", "-C", str(control), "credential", "fill"],
        input=(
            "protocol=https\n"
            "host=github.com\n"
            "path=relax-dot-gg/infra-management.git\n"
            "username=x-access-token\n\n"
        ),
        text=True,
        capture_output=True,
        check=False,
        env={**fetch_env, "HOME": str(credential_home), "GIT_TERMINAL_PROMPT": "0"},
    )
    assert credential.returncode == 0
    assert "password=test-token" in credential.stdout
    assert not unsafe_marker.exists()


def test_controller_refresh_does_not_fetch_a_raw_selected_revision(
    monkeypatch, tmp_path: Path
) -> None:
    control, remote, revision = _controller_clone_missing_selected_revision(tmp_path)
    commands: list[list[str]] = []
    real_run = subprocess.run

    def reject_raw_revision_fetch(
        args: list[str], **kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if "fetch" in args and revision in args:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="rejected"
            )
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._CONTROLLER_REFRESH_SOURCE_REMOTE", remote
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", reject_raw_revision_fetch
    )

    with _controller_refresh_source(control, revision) as source:
        assert _git(source, "rev-parse", "HEAD") == revision

    fetches = [args for args in commands if "fetch" in args]
    assert fetches
    assert all(revision not in args for args in fetches)


def test_controller_refresh_rejects_selected_revision_missing_from_expected_main(
    monkeypatch, tmp_path: Path
) -> None:
    control, remote, _revision = _controller_clone_missing_selected_revision(tmp_path)
    missing_revision = "a" * 40
    commands: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._CONTROLLER_REFRESH_SOURCE_REMOTE", remote
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", recording_run
    )

    with pytest.raises(CliFailure) as failed:
        with _controller_refresh_source(control, missing_revision):
            pass

    assert failed.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert not any("worktree" in args for args in commands)


def test_controller_refresh_rejects_wrong_remote_without_leaking_credentials(
    monkeypatch, tmp_path: Path
) -> None:
    control, _remote, revision = _controller_clone_missing_selected_revision(tmp_path)
    secret = "top-secret-token"
    _git(
        control, "remote", "set-url", "origin", f"https://operator:{secret}@wrong.example.invalid/x"
    )
    commands: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._CONTROLLER_REFRESH_SOURCE_REMOTE",
        "https://github.com/relax-dot-gg/infra-management.git",
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", recording_run
    )

    with pytest.raises(CliFailure) as failed:
        with _controller_refresh_source(control, revision):
            pass

    assert failed.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert secret not in failed.value.message
    assert secret not in failed.value.fix
    assert secret not in json.dumps(failed.value.details)
    assert not any("fetch" in args for args in commands)
    assert not any("worktree" in args for args in commands)


def test_controller_refresh_fetch_failure_does_not_materialize_or_leak_remote_output(
    monkeypatch, tmp_path: Path
) -> None:
    control, remote, revision = _controller_clone_missing_selected_revision(tmp_path)
    secret = "top-secret-token"
    commands: list[list[str]] = []
    real_run = subprocess.run

    def failing_fetch(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if "fetch" in args:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr=secret)
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._CONTROLLER_REFRESH_SOURCE_REMOTE", remote
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", failing_fetch
    )

    with pytest.raises(CliFailure) as failed:
        with _controller_refresh_source(control, revision):
            pass

    assert failed.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert secret not in failed.value.message
    assert secret not in failed.value.fix
    assert secret not in json.dumps(failed.value.details)
    assert not any("worktree" in args for args in commands)


def test_controller_refresh_fetch_failure_does_not_start_ansible(
    monkeypatch, tmp_path: Path
) -> None:
    control, remote, revision = _controller_clone_with_selected_revision_missing_from_main(tmp_path)
    commands: list[list[str]] = []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    context = Context()
    context.registry_path = tmp_path
    target = type("Target", (), {"uuid": HOST_ID})()
    monkeypatch.setattr("infralink.operator_operations.host_bootstrap._CONTROL_ROOT", control)
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._CONTROLLER_REFRESH_SOURCE_REMOTE", remote
    )
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap._controller_refresh_extra_vars",
        lambda *_args: (revision, {}),
    )
    monkeypatch.setattr("infralink.cli.operations.resolve_apply_request", lambda *_args: object())
    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", recording_run
    )

    with pytest.raises(CliFailure) as failed:
        _apply_controller_refresh(context, target, revision)

    assert failed.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert not any(args[0] == "ansible-playbook" for args in commands)


def test_controller_refresh_ignores_a_git_replacement_ref(monkeypatch, tmp_path: Path) -> None:
    repository, revision = _controller_source_repository(tmp_path)
    _configure_self_remote(monkeypatch, repository)
    playbook = repository / "ansible/playbooks/infralink_controller_refresh.yml"
    playbook.write_text("unsafe replacement\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "--quiet", "-m", "replacement")
    replacement = _git(repository, "rev-parse", "HEAD")
    _git(repository, "replace", revision, replacement)

    with _controller_refresh_source(repository, revision) as source:
        assert (source / "ansible/playbooks/infralink_controller_refresh.yml").read_text(
            encoding="utf-8"
        ) == "---\n- hosts: all\n"


def test_controller_refresh_reports_failed_temporary_worktree_cleanup(
    monkeypatch, tmp_path: Path
) -> None:
    repository, revision = _controller_source_repository(tmp_path)
    _configure_self_remote(monkeypatch, repository)
    real_run = subprocess.run

    def failing_cleanup(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-4:-1] == ["worktree", "remove", "--force"]:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="cleanup failed"
            )
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr(
        "infralink.operator_operations.host_bootstrap.subprocess.run", failing_cleanup
    )

    with pytest.raises(CliFailure) as cleanup:
        with _controller_refresh_source(repository, revision):
            pass
    assert cleanup.value.code == ErrorCode.ARTIFACT_IO_FAILED


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


def test_controller_refresh_prefers_the_explicit_host_runtime_over_global_lock(
    tmp_path: Path,
) -> None:
    """A host declaration may advance without changing the fleet-wide fallback."""
    from infralink.operator_operations.host_bootstrap import _controller_refresh_extra_vars

    registry_root = tmp_path / "registry"
    registry = registry_root / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    self_deploy_v2_reconcile_enabled: true\n"
        "    self_deploy_v2_reconcile_packaged: true\n"
        "    self_deploy_v2_promotion_policy_enabled: true\n"
        "    self_deploy_legacy_cron_enabled: false\n"
        "    self_deploy_v2_promotion_registry_remote: ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
        "    self_deploy_v2_promotion_bws_project_id: 11111111-1111-4111-8111-111111111111\n"
        "    self_deploy_v2_registry_read_identity_secret_uuid: 22222222-2222-4222-8222-222222222222\n"
        f"    self_deploy_v2_promotion_host_fingerprint: ssh-rsa {HOST_FINGERPRINT}\n"
        "    self_deploy_v2_promotion_allowed_signers: infra ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEjV/Mqc501uHt3OiM0aYthhtAHO1htXrDuEYh4UQOXI\n"
        "    self_deploy_v2_promotion_channel: core-v2\n"
        "    self_deploy_registry_origin: http://100.64.68.83:3000/relaxgg/infra-registry.git\n",
        encoding="utf-8",
    )
    explicit_revision = "b" * 40
    deployment = registry / HOST_ID / "operations" / "deployment.yml"
    deployment.parent.mkdir()
    deployment.write_text(f"infra_management:\n  revision: {explicit_revision}\n", encoding="utf-8")
    lock = registry_root / "operations" / "infra-management.lock"
    lock.parent.mkdir()
    lock.write_text("a" * 40 + "\n", encoding="utf-8")
    target = type("Target", (), {"uuid": HOST_ID, "canonical_name": HOST_NAME})()

    runtime_revision, _ = _controller_refresh_extra_vars(registry, target)

    assert runtime_revision == explicit_revision

    deployment.unlink()
    deployment.mkdir()
    with pytest.raises(CliFailure) as malformed:
        _controller_refresh_extra_vars(registry, target)

    assert malformed.value.code == ErrorCode.CONFIGURATION_REQUIRED
    assert malformed.value.details == {"path": str(deployment)}
