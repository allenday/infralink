from __future__ import annotations

from infralink.host_readiness import HostReadinessEvaluator, HostReadinessProbe
from infralink.host_transport import SshReadinessTransport


def _probe(**overrides: object) -> HostReadinessProbe:
    values: dict[str, object] = {
        "reachable": True,
        "hostname": "relaxgg-db-es1",
        "machine_id": "a2d6d6ac82af4f76a029d26361d003bf",
        "commands": {"git": True, "docker": False, "tailscale": True, "jq": False, "bws": False},
        "devops_account": False,
        "devops_authorized_access": False,
        "bws_config": False,
        "self_deploy_dependencies": False,
        "self_deploy_runtime": False,
        "self_deploy_timer_enabled": False,
        "self_deploy_timer_active": False,
        "registry_layout": "v2_managed",
        "error": None,
    }
    values.update(overrides)
    return HostReadinessProbe(**values)


def test_readiness_is_fail_closed_and_derives_only_failed_baseline_actions() -> None:
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(),
    )

    assert readiness.ready is False
    assert [check.id for check in readiness.checks if not check.passed] == [
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
    assert [action.check_id for action in readiness.actions] == [
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


def test_legacy_registry_layout_remains_healthy_without_declared_migration_policy() -> None:
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(registry_layout="legacy_nested"),
    )

    layout = next(check for check in readiness.checks if check.id == "registry_layout")
    assert layout.passed is True
    assert not any(action.id == "migrate_v2_registry_layout" for action in readiness.actions)


def test_legacy_registry_layout_requires_migration_only_when_declared() -> None:
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(registry_layout="legacy_nested", requires_v2_registry_layout=True),
    )

    layout = next(check for check in readiness.checks if check.id == "registry_layout")
    assert layout.passed is False
    assert layout.detail == "legacy_nested"
    assert any(action.id == "migrate_v2_registry_layout" for action in readiness.actions)


def test_v2_reconcile_terminal_failure_is_a_required_readiness_failure() -> None:
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(
            self_deploy_runtime=True,
            self_deploy_mode="v2_reconcile",
            self_deploy_timer_enabled=True,
            self_deploy_timer_active=True,
            self_deploy_reconcile_result="exit-code",
            self_deploy_reconcile_exit_status=1,
        ),
    )

    reconcile = next(check for check in readiness.checks if check.id == "self_deploy_reconcile")
    assert reconcile.passed is False
    assert reconcile.detail == "exit-code:1"
    assert any(action.id == "inspect_self_deploy_reconcile" for action in readiness.actions)


def test_v2_reconcile_success_satisfies_readiness() -> None:
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(
            self_deploy_runtime=True,
            self_deploy_mode="v2_reconcile",
            self_deploy_timer_enabled=True,
            self_deploy_timer_active=True,
            self_deploy_reconcile_result="success",
            self_deploy_reconcile_exit_status=0,
            self_deploy_reconcile_active_state="inactive",
            self_deploy_reconcile_sub_state="dead",
            self_deploy_reconcile_exit_timestamp_monotonic=123,
        ),
    )

    reconcile = next(check for check in readiness.checks if check.id == "self_deploy_reconcile")
    assert reconcile.passed is True


def test_provisioning_host_without_declared_reconcile_does_not_require_timer_or_run() -> None:
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        require_reconcile=False,
        probe=_probe(
            commands={"git": True, "docker": True, "tailscale": True, "jq": True, "bws": True},
            devops_account=True,
            devops_authorized_access=True,
            bws_config=True,
            self_deploy_dependencies=True,
            self_deploy_runtime=True,
            self_deploy_mode="v2_reconcile",
            self_deploy_timer_enabled=False,
            self_deploy_timer_active=False,
            self_deploy_reconcile_result="exit-code",
            self_deploy_reconcile_exit_status=1,
        ),
    )

    timer = next(check for check in readiness.checks if check.id == "self_deploy_timer")
    reconcile = next(check for check in readiness.checks if check.id == "self_deploy_reconcile")
    assert readiness.ready is True
    assert timer.required is False
    assert timer.passed is True
    assert reconcile.required is False
    assert reconcile.passed is True
    assert not any(
        action.check_id in {"self_deploy_timer", "self_deploy_reconcile"}
        for action in readiness.actions
    )


def test_v2_reconcile_never_started_fails_closed_despite_systemd_defaults() -> None:
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(
            self_deploy_runtime=True,
            self_deploy_mode="v2_reconcile",
            self_deploy_timer_enabled=True,
            self_deploy_timer_active=True,
            self_deploy_reconcile_result="success",
            self_deploy_reconcile_exit_status=0,
            self_deploy_reconcile_active_state="inactive",
            self_deploy_reconcile_sub_state="dead",
            self_deploy_reconcile_exit_timestamp_monotonic=0,
        ),
    )

    reconcile = next(check for check in readiness.checks if check.id == "self_deploy_reconcile")
    assert reconcile.passed is False
    assert reconcile.detail == "self_deploy_reconcile_not_completed"


def test_unreachable_host_fails_every_required_baseline_without_actions_that_claim_success() -> (
    None
):
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(reachable=False, hostname=None, machine_id=None, error="ssh_unreachable"),
    )

    assert readiness.ready is False
    assert all(not check.passed for check in readiness.checks)
    action = next(action for action in readiness.actions if action.check_id == "ssh_reachable")
    assert action.id == "establish_root_ssh"


def test_ssh_transport_parses_only_read_only_probe_output(monkeypatch) -> None:
    class Completed:
        returncode = 0
        stderr = ""
        stdout = """hostname=relaxgg-db-es1
machine_id=machine-id
git=1
docker=0
tailscale=1
jq=0
bws=0
devops_account=0
devops_authorized_access=0
bws_config=0
self_deploy_runtime=0
self_deploy_timer_enabled=0
self_deploy_timer_active=0
registry_layout=legacy_nested
self_deploy_reconcile_result=exit-code
self_deploy_reconcile_exit_status=1
self_deploy_reconcile_active_state=failed
self_deploy_reconcile_sub_state=failed
self_deploy_reconcile_exit_timestamp_monotonic=123
"""

    calls: list[object] = []
    monkeypatch.setattr(
        "infralink.host_transport.subprocess.run",
        lambda *args, **kwargs: calls.append((args, kwargs)) or Completed(),
    )

    probe = SshReadinessTransport().probe("100.64.68.83")

    assert probe.reachable is True
    assert probe.hostname == "relaxgg-db-es1"
    assert probe.commands == {
        "git": True,
        "docker": False,
        "tailscale": True,
        "jq": False,
        "bws": False,
    }
    argv, kwargs = calls[0]
    assert argv[0][:4] == ["ssh", "-o", "BatchMode=yes", "-o"]
    assert kwargs["shell"] is False
    assert probe.registry_layout == "legacy_nested"
    assert probe.self_deploy_reconcile_result == "exit-code"
    assert probe.self_deploy_reconcile_exit_status == 1
    assert probe.self_deploy_reconcile_active_state == "failed"
    assert probe.self_deploy_reconcile_sub_state == "failed"
    assert probe.self_deploy_reconcile_exit_timestamp_monotonic == 123


def test_ssh_transport_requires_a_nonempty_bws_token_in_etc_environment() -> None:
    from infralink.host_transport import _REMOTE_PROBE

    assert "grep -Eq '^[[:space:]]*BWS_ACCESS_TOKEN=.+' /etc/environment" in _REMOTE_PROBE
    assert "bws.json" not in _REMOTE_PROBE
    assert "bws.conf" not in _REMOTE_PROBE


def test_ssh_transport_probes_the_active_controller_units() -> None:
    from infralink.host_transport import _REMOTE_PROBE

    assert "infralink-host-reconcile.timer" in _REMOTE_PROBE
    assert "infralink-host-reconcile.service" in _REMOTE_PROBE
    assert "self-deploy-v2-reconcile" not in _REMOTE_PROBE
