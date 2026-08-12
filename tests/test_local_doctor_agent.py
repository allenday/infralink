from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner

import infralink.local_doctor_agent as agent
from infralink.host_readiness import HostReadinessProbe
from infralink.local_doctor import LocalDoctorResult
from infralink.local_doctor_agent import (
    LocalDoctorRuntimeConfig,
    RuntimeConfigTrustRoot,
    RuntimeOutputRoots,
    load_signed_runtime_config,
    main,
)


def test_local_probe_uses_the_active_controller_units() -> None:
    source = inspect.getsource(agent)
    assert "infralink-host-reconcile.timer" in source
    assert "infralink-host-reconcile.service" in source
    assert "self-deploy-v2-reconcile" not in source


def _runtime_payload(tmp_path: Path) -> dict[str, object]:
    return {
        "schema_version": "infralink.local-doctor-runtime/v1",
        "config": {
            "canonical_name": "node-1",
            "freshness_seconds": 120,
            "state_path": str(tmp_path / "state" / "latest.json"),
            "metrics_path": str(tmp_path / "metrics" / "doctor.prom"),
            "firewall_declaration_path": str(tmp_path / "runtime" / "firewall.json"),
            "firewall_allowed_signers_path": str(tmp_path / "runtime" / "firewall.allowed_signers"),
            "require_reconcile": True,
            "http_address": "100.64.0.1",
            "http_port": 9473,
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
        "infralink.local_doctor_agent.DEFAULT_OUTPUT_ROOTS",
        RuntimeOutputRoots(
            state_root=tmp_path / "state",
            metrics_root=tmp_path / "metrics",
            runtime_root=tmp_path / "runtime",
        ),
    )
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
            "--allowed-signers",
            str(allowed_signers),
            "--config",
            str(config),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == {
        "raw": f"infralink-local-doctor collect --allowed-signers {allowed_signers} --config {config}",
        "parsed": {
            "path": ["collect"],
            "args": {"allowed_signers": str(allowed_signers), "config": str(config)},
            "flags": ["--allowed-signers", "--config"],
        },
    }
    assert payload["next_actions"] == []
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
            output_roots=RuntimeOutputRoots(
                tmp_path / "state", tmp_path / "metrics", tmp_path / "runtime"
            ),
        )


def test_runtime_config_rejects_signed_paths_outside_approved_roots(tmp_path: Path) -> None:
    config = dict(_runtime_payload(tmp_path)["config"])  # type: ignore[arg-type]
    config["state_path"] = "/etc/infralink/local-doctor/latest.json"

    with pytest.raises(ValueError, match="runtime config"):
        LocalDoctorRuntimeConfig.from_dict(
            config,
            output_roots=RuntimeOutputRoots(
                state_root=tmp_path / "state",
                metrics_root=tmp_path / "metrics",
                runtime_root=tmp_path / "runtime",
            ),
        )


def test_collect_restores_previous_state_when_metric_publication_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "runtime.json"
    config.write_text(json.dumps(_runtime_payload(tmp_path)), encoding="utf-8")
    allowed_signers = tmp_path / "runtime.allowed_signers"
    allowed_signers.write_text("test signer\n", encoding="utf-8")
    monkeypatch.setattr("infralink.local_doctor_agent._verify_ssh_signature", lambda *args: True)
    monkeypatch.setattr(
        "infralink.local_doctor_agent.DEFAULT_OUTPUT_ROOTS",
        RuntimeOutputRoots(tmp_path / "state", tmp_path / "metrics", tmp_path / "runtime"),
    )
    previous = LocalDoctorResult.healthy(now=datetime.now(timezone.utc), freshness_seconds=60)
    from infralink.local_doctor import LatestResultStore

    state_store = LatestResultStore(tmp_path / "state" / "latest.json")
    state_store.write(previous)
    previous = state_store.load()
    monkeypatch.setattr(
        "infralink.local_doctor_agent.write_prometheus_textfile",
        lambda *args: (_ for _ in ()).throw(OSError("full")),
    )
    monkeypatch.setattr(
        "infralink.local_doctor_agent.LocalDoctorCollector.collect",
        lambda *args, **kwargs: LocalDoctorResult.unhealthy(
            now=datetime.now(timezone.utc), freshness_seconds=60
        ),
    )

    result = CliRunner().invoke(
        main, ["collect", "--config", str(config), "--allowed-signers", str(allowed_signers)]
    )

    assert result.exit_code == 2
    assert state_store.load() == previous


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("http_address", "0.0.0.0"),
        ("http_address", "example.internal"),
        ("http_address", "999.1.1.1"),
        ("http_port", 0),
        ("http_port", 65536),
    ],
)
def test_runtime_config_requires_a_declared_valid_http_binding(
    tmp_path: Path, field: str, value: object
) -> None:
    config = dict(_runtime_payload(tmp_path)["config"])  # type: ignore[arg-type]
    config[field] = value

    with pytest.raises(ValueError, match="runtime config"):
        LocalDoctorRuntimeConfig.from_dict(
            config,
            output_roots=RuntimeOutputRoots(
                tmp_path / "state", tmp_path / "metrics", tmp_path / "runtime"
            ),
        )


def test_serve_reports_a_structured_error_when_the_declared_binding_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = LocalDoctorRuntimeConfig(
        canonical_name="node-1",
        freshness_seconds=120,
        state_path=tmp_path / "state" / "latest.json",
        metrics_path=tmp_path / "metrics" / "doctor.prom",
        firewall_declaration_path=tmp_path / "runtime" / "firewall.json",
        firewall_allowed_signers_path=tmp_path / "runtime" / "firewall.allowed_signers",
        require_reconcile=True,
        http_address="100.64.0.1",
        http_port=9473,
    )
    monkeypatch.setattr("infralink.local_doctor_agent._runtime_or_error", lambda *args: runtime)
    monkeypatch.setattr(
        "infralink.local_doctor_agent.serve_latest_result",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("address in use")),
    )

    result = CliRunner().invoke(
        main,
        [
            "serve",
            "--config",
            str(tmp_path / "runtime.json"),
            "--allowed-signers",
            str(tmp_path / "signers"),
        ],
    )

    assert result.exit_code == 2
    assert json.loads(result.output)["error"]["code"] == "runtime_bind_failed"
