from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml
from click.testing import CliRunner

from infralink.cli.host_readiness import evaluate_host_readiness as evaluate_readiness
from infralink.cli.main import BASELINE_EXECUTOR_ACTIONS, cli
from infralink.host_readiness import HostReadinessProbe

ROOT = Path(__file__).resolve().parents[1]


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


def test_host_bootstrap_apply_never_sends_manual_secret_or_runtime_actions(monkeypatch) -> None:
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
    assert "enable_self_deploy_timer" not in serialized
