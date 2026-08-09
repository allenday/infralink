from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import yaml
from click.testing import CliRunner

from infralink.cli.main import cli
from infralink.host_readiness import HostReadinessProbe

ROOT = Path(__file__).resolve().parents[1]


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
        "devops_account",
        "devops_authorized_access",
        "docker",
        "jq",
        "bws_cli",
        "bws_config",
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

    follow_up = CliRunner().invoke(
        cli, shlex.split(payload["next_actions"][0]["command"])[1:]
    )
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
            "--registry", str(ROOT / "examples" / "registry.yml"),
            "--edges", str(ROOT / "examples" / "edges.yml"),
            "host", "bootstrap", "database.example.com", "--apply",
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
