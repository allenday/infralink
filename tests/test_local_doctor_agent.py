from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from infralink.host_readiness import HostReadinessProbe
from infralink.local_doctor import LocalDoctorResult
from infralink.local_doctor_agent import (
    LocalDoctorRuntimeConfig,
    RuntimeConfigTrustRoot,
    load_signed_runtime_config,
    main,
)


def _runtime_payload(tmp_path: Path) -> dict[str, object]:
    return {
        "schema_version": "infralink.local-doctor-runtime/v1",
        "config": {
            "canonical_name": "node-1",
            "freshness_seconds": 120,
            "state_path": str(tmp_path / "state" / "latest.json"),
            "metrics_path": str(tmp_path / "metrics" / "doctor.prom"),
            "firewall_declaration_path": str(tmp_path / "firewall.json"),
            "firewall_allowed_signers_path": str(tmp_path / "firewall.allowed_signers"),
            "require_reconcile": True,
        },
        "signature": "verified-signature",
    }


def test_runtime_config_requires_a_concrete_allowed_signers_trust_root(tmp_path: Path) -> None:
    config = tmp_path / "runtime.json"
    config.write_text(json.dumps(_runtime_payload(tmp_path)), encoding="utf-8")

    with pytest.raises(ValueError, match="allowed signers"):
        load_signed_runtime_config(
            config,
            trust_root=RuntimeConfigTrustRoot(tmp_path / "missing.allowed_signers"),
        )


def test_collect_verifies_runtime_config_and_writes_state_and_metrics_atomically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "runtime.json"
    config.write_text(json.dumps(_runtime_payload(tmp_path)), encoding="utf-8")
    allowed_signers = tmp_path / "runtime.allowed_signers"
    allowed_signers.write_text("test signer\n", encoding="utf-8")
    monkeypatch.setattr("infralink.local_doctor_agent._verify_ssh_signature", lambda *args: True)
    monkeypatch.setattr(
        "infralink.local_doctor_agent.collect_local_readiness_probe",
        lambda: HostReadinessProbe(
            reachable=True,
            hostname="node-1",
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
            registry_layout="v2_managed",
        ),
    )
    monkeypatch.setattr(
        "infralink.local_doctor.load_signed_firewall_declaration",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "infralink.local_doctor.NftablesFirewallEvaluator.evaluate",
        lambda *args: (),
    )

    result = CliRunner().invoke(
        main,
        [
            "collect",
            "--config",
            str(config),
            "--allowed-signers",
            str(allowed_signers),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["result"]["status"] == "healthy"
    state = LocalDoctorResult.from_dict(
        json.loads((tmp_path / "state" / "latest.json").read_text(encoding="utf-8"))
    )
    assert state.status == "healthy"
    metrics = (tmp_path / "metrics" / "doctor.prom").read_text(encoding="utf-8")
    assert "infralink_local_doctor_converged 1" in metrics


def test_collect_refuses_an_invalid_runtime_signature_before_running_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "runtime.json"
    config.write_text(json.dumps(_runtime_payload(tmp_path)), encoding="utf-8")
    allowed_signers = tmp_path / "runtime.allowed_signers"
    allowed_signers.write_text("test signer\n", encoding="utf-8")
    monkeypatch.setattr("infralink.local_doctor_agent._verify_ssh_signature", lambda *args: False)
    monkeypatch.setattr(
        "infralink.local_doctor_agent.collect_local_readiness_probe",
        lambda: pytest.fail("probe must not run before runtime signature verification"),
    )

    result = CliRunner().invoke(
        main,
        ["collect", "--config", str(config), "--allowed-signers", str(allowed_signers)],
    )

    assert result.exit_code != 0
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert payload["error"]["code"] == "runtime_config_invalid"


def test_runtime_config_rejects_relative_paths(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="runtime config"):
        LocalDoctorRuntimeConfig.from_dict(
            {
                **_runtime_payload(tmp_path)["config"],  # type: ignore[arg-type]
                "state_path": "state/latest.json",
            },
            runtime_root=tmp_path,
        )
