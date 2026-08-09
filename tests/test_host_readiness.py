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
        "self_deploy_runtime": False,
        "self_deploy_timer_enabled": False,
        "self_deploy_timer_active": False,
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
        "self_deploy_runtime",
        "self_deploy_timer",
    ]


def test_unreachable_host_fails_every_required_baseline_without_actions_that_claim_success() -> (
    None
):
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name="relaxgg-db-es1",
        probe=_probe(reachable=False, hostname=None, machine_id=None, error="ssh_unreachable"),
    )

    assert readiness.ready is False
    assert all(not check.passed for check in readiness.checks)
    assert readiness.actions[0].check_id == "ssh_reachable"
    assert readiness.actions[0].id == "establish_root_ssh"


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


def test_ssh_transport_requires_a_nonempty_bws_token_in_etc_environment() -> None:
    from infralink.host_transport import _REMOTE_PROBE

    assert "grep -Eq '^[[:space:]]*BWS_ACCESS_TOKEN=.+' /etc/environment" in _REMOTE_PROBE
    assert "bws.json" not in _REMOTE_PROBE
    assert "bws.conf" not in _REMOTE_PROBE
