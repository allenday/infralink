from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from infralink.cli.contracts import HostBootstrapAction, HostReadinessResult
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.host_readiness import evaluate_host_readiness as evaluate_readiness
from infralink.cli.main import (
    BASELINE_EXECUTOR_ACTIONS,
    Context,
    _apply_controller_refresh,
    _controller_refresh_source,
    cli,
)
from infralink.host_readiness import HostReadinessProbe

ROOT = Path(__file__).resolve().parents[1]
HOST_ID = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
HOST_NAME = "database.example.com"
HOST_FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


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


def test_controller_refresh_materializes_an_exact_detached_source(tmp_path: Path) -> None:
    repository, revision = _controller_source_repository(tmp_path)

    with _controller_refresh_source(repository, revision) as source:
        assert source != repository
        assert _git(source, "rev-parse", "HEAD") == revision
        assert (source / "ansible/playbooks/infralink_controller_refresh.yml").is_file()


def test_controller_refresh_rejects_a_dirty_or_missing_controller_source(tmp_path: Path) -> None:
    repository, revision = _controller_source_repository(tmp_path)
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


def test_controller_refresh_fetches_only_the_absent_selected_revision(
    monkeypatch, tmp_path: Path
) -> None:
    control, remote, revision = _controller_clone_missing_selected_revision(tmp_path)
    commands: list[tuple[list[str], dict[str, object]]] = []
    real_run = subprocess.run

    def recording_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append((args, kwargs))
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr("infralink.cli.main._CONTROLLER_REFRESH_SOURCE_REMOTE", remote)
    monkeypatch.setattr("infralink.cli.main.subprocess.run", recording_run)

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
            revision,
        ]
    ]
    fetch_env = next(kwargs["env"] for args, kwargs in commands if "fetch" in args)
    assert isinstance(fetch_env, dict)
    assert fetch_env["GIT_NO_REPLACE_OBJECTS"] == "1"
    assert fetch_env["GIT_CONFIG_GLOBAL"] == "/dev/null"


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
        "infralink.cli.main._CONTROLLER_REFRESH_SOURCE_REMOTE",
        "https://github.com/relax-dot-gg/infra-management.git",
    )
    monkeypatch.setattr("infralink.cli.main.subprocess.run", recording_run)

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

    monkeypatch.setattr("infralink.cli.main._CONTROLLER_REFRESH_SOURCE_REMOTE", remote)
    monkeypatch.setattr("infralink.cli.main.subprocess.run", failing_fetch)

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
    control, remote, revision = _controller_clone_missing_selected_revision(tmp_path)
    commands: list[list[str]] = []
    real_run = subprocess.run

    def failing_fetch(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        commands.append(args)
        if "fetch" in args:
            return subprocess.CompletedProcess(args=args, returncode=1, stdout="", stderr="failed")
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    context = Context()
    context.registry_path = tmp_path
    target = type("Target", (), {"uuid": HOST_ID})()
    monkeypatch.setattr("infralink.cli.main._CONTROL_ROOT", control)
    monkeypatch.setattr("infralink.cli.main._CONTROLLER_REFRESH_SOURCE_REMOTE", remote)
    monkeypatch.setattr(
        "infralink.cli.main._controller_refresh_extra_vars", lambda *_args: (revision, {})
    )
    monkeypatch.setattr("infralink.cli.operations.resolve_apply_request", lambda *_args: object())
    monkeypatch.setattr("infralink.cli.main.subprocess.run", failing_fetch)

    with pytest.raises(CliFailure) as failed:
        _apply_controller_refresh(context, target, revision)

    assert failed.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert not any(args[0] == "ansible-playbook" for args in commands)


def test_controller_refresh_ignores_a_git_replacement_ref(tmp_path: Path) -> None:
    repository, revision = _controller_source_repository(tmp_path)
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
    real_run = subprocess.run

    def failing_cleanup(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[-4:-1] == ["worktree", "remove", "--force"]:
            return subprocess.CompletedProcess(
                args=args, returncode=1, stdout="", stderr="cleanup failed"
            )
        return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]

    monkeypatch.setattr("infralink.cli.main.subprocess.run", failing_cleanup)

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


def test_host_bootstrap_plan_uses_the_same_failed_readiness_checks(monkeypatch) -> None:
    monkeypatch.setattr(
        "infralink.cli.main.SshReadinessTransport.probe",
        lambda self, address: HostReadinessProbe(
            reachable=True,
            hostname="database.example.com",
            machine_id="machine-id",
            commands={"git": True, "docker": False, "tailscale": True, "jq": False, "bws": False},
            devops_account=False,
            devops_authorized_access=False,
            bws_config=False,
            self_deploy_runtime=False,
            self_deploy_timer_enabled=False,
            self_deploy_timer_active=False,
            error=None,
        ),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(ROOT / "examples" / "registry.yml"),
            "--edges",
            str(ROOT / "examples" / "edges.yml"),
            "host",
            "bootstrap",
            "database.example.com",
            "--plan",
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["readiness"]["ready"] is False
    assert [item["check_id"] for item in payload["result"]["readiness"]["actions"]] == [
        "registry_layout",
        "devops_account",
        "devops_authorized_access",
        "docker",
        "jq",
        "bws_cli",
        "bws_config",
        "self_deploy_dependencies",
        "self_deploy_runtime",
        "self_deploy_timer",
    ]
    # A bootstrap-plan response must link to an action that can be rerun using
    # only the topology sources it already carries; full doctor also requires
    # observation/Gatus configuration.
    assert payload["next_actions"][0]["rel"] == "reinspect-readiness"
    assert payload["next_actions"][0]["command"].endswith(
        "host bootstrap d1b9e5d5-36b0-459d-a556-96622811fbd5 --plan"
    )
    assert payload["next_actions"][0]["safe"] is True

    follow_up = CliRunner().invoke(cli, shlex.split(payload["next_actions"][0]["command"])[1:])
    assert follow_up.exit_code == 0
    assert json.loads(follow_up.output)["result"]["readiness"] == payload["result"]["readiness"]


def test_host_bootstrap_links_verifier_when_reconcile_failed(monkeypatch) -> None:
    monkeypatch.setattr(
        "infralink.cli.main.SshReadinessTransport.probe",
        lambda self, address: HostReadinessProbe(
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
            self_deploy_mode="v2_reconcile",
            registry_layout="v2_managed",
            self_deploy_reconcile_result="exit-code",
            self_deploy_reconcile_exit_status=1,
        ),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(ROOT / "examples" / "registry.yml"),
            "--edges",
            str(ROOT / "examples" / "edges.yml"),
            "host",
            "bootstrap",
            "database.example.com",
            "--plan",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 0
    verifier_action = next(item for item in payload["next_actions"] if item["rel"] == "verifier")
    assert verifier_action["command"].endswith("host verifier d1b9e5d5-36b0-459d-a556-96622811fbd5")


def test_host_bootstrap_help_marks_plan_required_and_shows_an_example() -> None:
    result = CliRunner().invoke(cli, ["--output", "json", "help", "host", "bootstrap"])
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["options"] == [
        {"name": "plan", "type": "boolean", "required": False},
        {"name": "apply", "type": "boolean", "required": False},
    ]
    assert payload["result"]["examples"] == [
        "infralink host bootstrap host-1 --plan",
        "infralink host bootstrap host-1 --apply",
    ]


def test_real_module_apply_failure_emits_an_envelope_and_nonzero_exit() -> None:
    environment = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "infralink",
            "--registry",
            str(ROOT / "examples" / "registry.yml"),
            "--edges",
            str(ROOT / "examples" / "edges.yml"),
            "host",
            "bootstrap",
            "database.example.com",
            "--apply",
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 4
    payload = yaml.safe_load(result.stdout)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "provider_unavailable"


def test_host_bootstrap_apply_missing_bastion_executor_is_provider_unavailable(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("infralink.cli.main._CONTROL_ROOT", tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(ROOT / "examples" / "registry.yml"),
            "--edges",
            str(ROOT / "examples" / "edges.yml"),
            "host",
            "bootstrap",
            "database.example.com",
            "--apply",
        ],
    )

    assert result.exit_code == 4
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "provider_unavailable"
    assert payload["error"]["details"] == {"capability": "host_bootstrap"}


def test_host_bootstrap_apply_never_sends_manual_secret_or_runtime_actions(
    monkeypatch, tmp_path: Path
) -> None:
    readiness = HostReadinessProbe(
        reachable=True,
        hostname="database.example.com",
        machine_id="machine-id",
        commands={"git": True, "docker": True, "tailscale": True, "jq": True, "bws": False},
        devops_account=True,
        devops_authorized_access=True,
        bws_config=False,
        self_deploy_runtime=False,
        self_deploy_timer_enabled=False,
        self_deploy_timer_active=False,
        error=None,
    )
    monkeypatch.setattr(
        "infralink.cli.main.evaluate_host_readiness",
        lambda *_args: evaluate_readiness(
            type(
                "Host",
                (),
                {
                    "canonical_name": "database.example.com",
                    "tailscale_ip": "192.0.2.10",
                    "public_ip": None,
                },
            )(),
            type("Transport", (), {"probe": lambda _self, _address: readiness})(),
        ),
    )
    control_root = tmp_path / "control"
    playbook = control_root / "ansible/playbooks/infralink_host_baseline.yml"
    playbook.parent.mkdir(parents=True)
    playbook.touch()
    monkeypatch.setattr("infralink.cli.main._CONTROL_ROOT", control_root)
    calls: list[object] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "infralink.cli.main.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Completed(),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(ROOT / "examples" / "registry.yml"),
            "--edges",
            str(ROOT / "examples" / "edges.yml"),
            "host",
            "bootstrap",
            "database.example.com",
            "--apply",
        ],
    )

    assert result.exit_code == 1
    payload = json.loads(result.output)
    guidance = {item["id"] for item in payload["result"]["readiness"]["actions"]}
    assert "migrate_v2_registry_layout" in guidance
    argv, _kwargs = calls[0]
    serialized = " ".join(str(item) for item in argv[0])
    forwarded = json.loads(argv[0][-1])["bootstrap_actions"]
    assert set(forwarded) <= BASELINE_EXECUTOR_ACTIONS
    assert "migrate_v2_registry_layout" not in forwarded
    assert "install_bws_cli" in serialized
    assert "configure_bws" not in serialized
    assert "install_self_deploy_runtime" not in serialized
    assert "enable_self_deploy_timer" in serialized


def test_baseline_executor_mirrors_the_v2_timer_capability() -> None:
    assert "enable_self_deploy_timer" in BASELINE_EXECUTOR_ACTIONS


def test_host_bootstrap_apply_forwards_only_the_timer_action_when_it_alone_is_missing(
    monkeypatch, tmp_path: Path
) -> None:
    readiness = HostReadinessProbe(
        reachable=True,
        hostname="database.example.com",
        machine_id="machine-id",
        commands={"git": True, "docker": True, "tailscale": True, "jq": True, "bws": True},
        devops_account=True,
        devops_authorized_access=True,
        bws_config=True,
        self_deploy_dependencies=True,
        self_deploy_runtime=True,
        self_deploy_timer_enabled=False,
        self_deploy_timer_active=False,
        error=None,
    )
    monkeypatch.setattr(
        "infralink.cli.main.evaluate_host_readiness",
        lambda *_args: evaluate_readiness(
            type(
                "Host",
                (),
                {
                    "canonical_name": "database.example.com",
                    "tailscale_ip": "192.0.2.10",
                    "public_ip": None,
                },
            )(),
            type("Transport", (), {"probe": lambda _self, _address: readiness})(),
        ),
    )
    control_root = tmp_path / "control"
    playbook = control_root / "ansible/playbooks/infralink_host_baseline.yml"
    playbook.parent.mkdir(parents=True)
    playbook.touch()
    monkeypatch.setattr("infralink.cli.main._CONTROL_ROOT", control_root)
    calls: list[object] = []

    class Completed:
        returncode = 0
        stdout = ""
        stderr = ""

    monkeypatch.setattr(
        "infralink.cli.main.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Completed(),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(ROOT / "examples" / "registry.yml"),
            "--edges",
            str(ROOT / "examples" / "edges.yml"),
            "host",
            "bootstrap",
            "database.example.com",
            "--apply",
        ],
    )

    # The executor succeeded, but this response still represents the pre-apply
    # readiness probe and therefore remains negative until the next probe.
    assert result.exit_code == 1
    argv, _kwargs = calls[0]
    assert json.loads(argv[0][-1])["bootstrap_actions"] == ["enable_self_deploy_timer"]


def test_host_bootstrap_apply_refreshes_only_the_pinned_controller_runtime(
    monkeypatch, tmp_path: Path
) -> None:
    registry_root = tmp_path / "registry"
    registry = registry_root / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    tailscale_ip: 100.64.68.83\n"
        f"    self_deploy_v2_promotion_host_fingerprint: ssh-rsa {HOST_FINGERPRINT}\n"
        "    self_deploy_v2_promotion_channel: core-v2\n"
        "    self_deploy_v2_promotion_policy_enabled: true\n"
        "    self_deploy_v2_reconcile_enabled: true\n"
        "    self_deploy_v2_reconcile_packaged: true\n"
        "    self_deploy_legacy_cron_enabled: false\n"
        "    self_deploy_v2_promotion_registry_remote: ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
        "    self_deploy_v2_promotion_bws_project_id: 11111111-1111-4111-8111-111111111111\n"
        "    self_deploy_v2_registry_read_identity_secret_uuid: 22222222-2222-4222-8222-222222222222\n"
        "    self_deploy_v2_promotion_allowed_signers: infra ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEjV/Mqc501uHt3OiM0aYthhtAHO1htXrDuEYh4UQOXI\n"
        "    self_deploy_registry_origin: http://100.64.68.83:3000/relaxgg/infra-registry.git\n",
        encoding="utf-8",
    )
    lock = registry_root / "operations" / "infra-management.lock"
    lock.parent.mkdir()
    runtime_revision = "b" * 40
    lock.write_text(runtime_revision + "\n", encoding="utf-8")
    control_root = tmp_path / "control"
    playbook = control_root / "ansible/playbooks/infralink_controller_refresh.yml"
    playbook.parent.mkdir(parents=True)
    playbook.touch()
    calls: list[tuple[list[str], dict[str, object]]] = []

    readiness = HostReadinessResult(
        transport="root_ssh",
        ready=False,
        checks=[],
        actions=[
            HostBootstrapAction(
                id="inspect_self_deploy_reconcile",
                check_id="self_deploy_reconcile",
                description="Refresh the legacy controller runtime.",
            )
        ],
        runtime_mode="legacy_pull",
        registry_layout="legacy_nested",
        self_deploy_reconcile_result="exit-code",
        self_deploy_reconcile_exit_status=1,
    )
    monkeypatch.setattr("infralink.cli.main.evaluate_host_readiness", lambda *_args: readiness)
    monkeypatch.setattr("infralink.cli.main._CONTROL_ROOT", control_root)
    monkeypatch.setattr(
        "infralink.cli.main._controller_refresh_source",
        lambda _root, _revision: nullcontext(control_root),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda _request: __import__("contextlib").nullcontext(tmp_path / "known_hosts"),
    )
    monkeypatch.setattr(
        "infralink.cli.main.subprocess.run",
        lambda args, **kwargs: (
            calls.append((args, kwargs))
            or subprocess.CompletedProcess(args=args, returncode=0, stdout="", stderr="")
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["--output", "json", "--registry", str(registry), "host", "bootstrap", HOST_ID, "--apply"],
    )

    assert result.exit_code == 1
    ansible_calls = [call for call in calls if call[0][0] == "ansible-playbook"]
    assert len(ansible_calls) == 1
    argv, kwargs = ansible_calls[0]
    assert argv[:7] == [
        "ansible-playbook",
        "-i",
        "100.64.68.83,",
        "-u",
        "root",
        "--ssh-common-args",
        "-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes "
        f"-o UserKnownHostsFile={tmp_path / 'known_hosts'}",
    ]
    assert argv[7] == str(playbook)
    assert kwargs["cwd"] == control_root
    extra_vars = json.loads(argv[-1])
    assert extra_vars["self_deploy_v2_runtime_revision"] == runtime_revision
    assert extra_vars["uuid"] == HOST_ID
    assert "bws_access_token" not in extra_vars
    assert "BWS_ACCESS_TOKEN" not in " ".join(argv)


def test_host_bootstrap_plan_reports_the_selected_controller_refresh_without_running_it(
    monkeypatch, tmp_path: Path
) -> None:
    registry_root = tmp_path / "registry"
    registry = registry_root / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    tailscale_ip: 100.64.68.83\n"
        f"    self_deploy_v2_promotion_host_fingerprint: ssh-rsa {HOST_FINGERPRINT}\n"
        "    self_deploy_v2_promotion_channel: core-v2\n"
        "    self_deploy_v2_promotion_policy_enabled: true\n"
        "    self_deploy_v2_reconcile_enabled: true\n"
        "    self_deploy_v2_reconcile_packaged: true\n"
        "    self_deploy_legacy_cron_enabled: false\n"
        "    self_deploy_v2_promotion_registry_remote: ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
        "    self_deploy_v2_promotion_bws_project_id: 11111111-1111-4111-8111-111111111111\n"
        "    self_deploy_v2_registry_read_identity_secret_uuid: 22222222-2222-4222-8222-222222222222\n"
        "    self_deploy_v2_promotion_allowed_signers: infra ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEjV/Mqc501uHt3OiM0aYthhtAHO1htXrDuEYh4UQOXI\n"
        "    self_deploy_registry_origin: http://100.64.68.83:3000/relaxgg/infra-registry.git\n",
        encoding="utf-8",
    )
    lock = registry_root / "operations" / "infra-management.lock"
    lock.parent.mkdir()
    runtime_revision = "b" * 40
    lock.write_text(runtime_revision + "\n", encoding="utf-8")
    readiness = HostReadinessResult(
        transport="root_ssh",
        ready=False,
        checks=[],
        actions=[
            HostBootstrapAction(
                id="inspect_self_deploy_reconcile",
                check_id="self_deploy_reconcile",
                description="Refresh the legacy controller runtime.",
            )
        ],
    )
    monkeypatch.setattr("infralink.cli.main.evaluate_host_readiness", lambda *_args: readiness)
    monkeypatch.setattr(
        "infralink.cli.main.subprocess.run",
        lambda *_args, **_kwargs: pytest.fail("host bootstrap --plan must not execute a runner"),
    )

    result = CliRunner().invoke(
        cli,
        ["--output", "json", "--registry", str(registry), "host", "bootstrap", HOST_ID, "--plan"],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    refresh = next(item for item in payload["next_actions"] if item["rel"] == "refresh-controller")
    assert refresh["safe"] is False
    assert runtime_revision in refresh["description"]
    assert refresh["command"].endswith(f"host bootstrap {HOST_ID} --apply")


def test_host_bootstrap_controller_refresh_failure_does_not_reprobe_or_start_services(
    monkeypatch, tmp_path: Path
) -> None:
    registry_root = tmp_path / "registry"
    registry = registry_root / "hosts"
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    tailscale_ip: 100.64.68.83\n"
        f"    self_deploy_v2_promotion_host_fingerprint: ssh-rsa {HOST_FINGERPRINT}\n"
        "    self_deploy_v2_promotion_channel: core-v2\n"
        "    self_deploy_v2_promotion_policy_enabled: true\n"
        "    self_deploy_v2_reconcile_enabled: true\n"
        "    self_deploy_v2_reconcile_packaged: true\n"
        "    self_deploy_legacy_cron_enabled: false\n"
        "    self_deploy_v2_promotion_registry_remote: ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
        "    self_deploy_v2_promotion_bws_project_id: 11111111-1111-4111-8111-111111111111\n"
        "    self_deploy_v2_registry_read_identity_secret_uuid: 22222222-2222-4222-8222-222222222222\n"
        "    self_deploy_v2_promotion_allowed_signers: infra ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIEjV/Mqc501uHt3OiM0aYthhtAHO1htXrDuEYh4UQOXI\n"
        "    self_deploy_registry_origin: http://100.64.68.83:3000/relaxgg/infra-registry.git\n",
        encoding="utf-8",
    )
    lock = registry_root / "operations" / "infra-management.lock"
    lock.parent.mkdir()
    lock.write_text("b" * 40 + "\n", encoding="utf-8")
    control_root = tmp_path / "control"
    playbook = control_root / "ansible/playbooks/infralink_controller_refresh.yml"
    playbook.parent.mkdir(parents=True)
    playbook.touch()
    readiness = HostReadinessResult(
        transport="root_ssh",
        ready=False,
        checks=[],
        actions=[
            HostBootstrapAction(
                id="inspect_self_deploy_reconcile",
                check_id="self_deploy_reconcile",
                description="Refresh the legacy controller runtime.",
            )
        ],
    )
    probes = 0

    def fake_readiness(*_args: object) -> HostReadinessResult:
        nonlocal probes
        probes += 1
        return readiness

    monkeypatch.setattr("infralink.cli.main.evaluate_host_readiness", fake_readiness)
    monkeypatch.setattr("infralink.cli.main._CONTROL_ROOT", control_root)
    monkeypatch.setattr(
        "infralink.cli.main._controller_refresh_source",
        lambda _root, _revision: nullcontext(control_root),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda _request: __import__("contextlib").nullcontext(tmp_path / "known_hosts"),
    )
    monkeypatch.setattr(
        "infralink.cli.main.subprocess.run",
        lambda args, **_kwargs: subprocess.CompletedProcess(
            args=args,
            returncode=0 if args[0] == "git" else 1,
            stdout="",
            stderr="",
        ),
    )

    result = CliRunner().invoke(
        cli,
        ["--output", "json", "--registry", str(registry), "host", "bootstrap", HOST_ID, "--apply"],
    )

    assert result.exit_code == 4
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "provider_unavailable"
    assert payload["error"]["details"]["host"] == HOST_ID
    assert probes == 1
