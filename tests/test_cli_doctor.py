from __future__ import annotations

import json
import shlex
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from infralink.cli.main import cli

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
HOST_ID = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
EDGE_ID = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"
OBSERVATION_ID = "declared-gatus-edge"


def _sources() -> list[str]:
    return [
        "--registry",
        str(EXAMPLES / "registry.yml"),
        "--edges",
        str(EXAMPLES / "edges.yml"),
    ]


def _observation_inputs(tmp_path: Path) -> tuple[Path, Path]:
    plan = tmp_path / "core-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": "infralink.observation-plan/v1",
                "dependencies": [
                    {
                        "id": OBSERVATION_ID,
                        "source_service_id": "fa2b9872-d94c-4b20-a73a-57a205560769/app-worker",
                        "target_service_id": f"{HOST_ID}/postgresql",
                        "target_endpoint_id": f"{HOST_ID}/postgresql/tcp",
                        "required": True,
                        "execution_adapter": "gatus",
                        "health_signal_refs": [f"dependency/{OBSERVATION_ID}/health/reachable"],
                    }
                ],
                "service_profiles": [{"id": "postgres-database"}],
                "services": [{"id": f"{HOST_ID}/postgresql", "profile_id": "postgres-database"}],
            }
        ),
        encoding="utf-8",
    )
    bindings = tmp_path / "adapter-bindings.yml"
    bindings.write_text(
        yaml.safe_dump(
            {
                "schema_version": "infra-observe.adapter-bindings.v1",
                "bindings": [
                    {
                        "id": f"gatus-{OBSERVATION_ID}",
                        "renderer_kind": "gatus",
                        "observation_backend_id": "core-health",
                        "output_identity": OBSERVATION_ID,
                        "signal_ref": f"dependency/{OBSERVATION_ID}/health/reachable",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return plan, bindings


def _invoke(*args: str):
    return CliRunner().invoke(cli, ["--output", "json", *_sources(), "doctor", *args])


def test_global_doctor_requires_declared_observation_inputs() -> None:
    help_result = CliRunner().invoke(cli, ["help"])
    help_payload = yaml.safe_load(help_result.output)
    assert "doctor" in {child["name"] for child in help_payload["result"]["children"]}

    result = _invoke()
    payload = json.loads(result.output)
    assert result.exit_code == 2
    assert payload["error"]["code"] == "configuration_required"
    assert payload["error"]["details"] == {"source": "observation_plan"}
    assert payload["command"]["resolved"]["gatus_configured"] is False


def test_doctor_validate_host_summarizes_normal_unknown_evidence_without_network_calls(
    tmp_path: Path, monkeypatch
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    monkeypatch.setattr(
        "infralink.health.checks.check_edge_health",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local probe forbidden")),
    )

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "host",
        "database.example.com",
        "--validate",
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["target"] == {
        "type": "host",
        "id": HOST_ID,
        "canonical_name": "database.example.com",
    }
    assert payload["result"]["coverage"] == {
        "required": 1,
        "bound": 1,
        "unbound": 0,
        "unsupported": 0,
        "valid": True,
    }
    assert payload["result"]["evidence"] == []
    assert payload["result"]["status"] == "unknown"
    assert payload["result"]["reason"] == "gatus_not_configured"
    assert payload["result"]["evidence_summary"] == [
        {
            "adapter": "gatus",
            "configured": False,
            "healthy": 0,
            "unhealthy": 0,
            "unavailable": 0,
            "unknown": 1,
            "live_observation_count": 0,
            "latest_observed_at": None,
        }
    ]
    configure = next(item for item in payload["next_actions"] if item["rel"] == "configure-gatus")
    assert configure["description"] == "Set INFRALINK_GATUS_URL or pass --gatus-url"
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/doctor.json").read_text())
    Draft202012Validator(schema).validate(payload)


def test_normal_doctor_requires_a_configured_gatus_observer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, bindings = _observation_inputs(tmp_path)

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "edge",
        EDGE_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "configuration_required"
    assert payload["error"]["details"] == {"source": "gatus_url"}
    assert payload["command"]["resolved"]["gatus_configured"] is False


def test_verbose_doctor_includes_all_declared_evidence(tmp_path: Path) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--verbose",
            *_sources(),
            "doctor",
            "--observation-plan",
            str(plan),
            "--adapter-bindings",
            str(bindings),
            "edge",
            EDGE_ID,
        ],
    )
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["error"]["details"] == {"source": "gatus_url"}


def test_doctor_profile_resolves_declared_observation_profile_not_service_or_role_name(
    tmp_path: Path,
) -> None:
    plan, bindings = _observation_inputs(tmp_path)

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "profile",
        "postgres-database",
        "--validate",
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["target"] == {
        "type": "profile",
        "id": "postgres-database",
        "canonical_name": None,
    }
    assert payload["result"]["coverage"]["valid"] is True


def test_doctor_service_resolves_a_host_qualified_logical_service(tmp_path: Path) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    payload = json.loads(plan.read_text(encoding="utf-8"))
    logical_service_id = f"{HOST_ID}/database-stack"
    payload["logical_services"] = [
        {
            "id": logical_service_id,
            "host_id": HOST_ID,
            "primary_service_id": f"{HOST_ID}/postgresql",
            "component_service_ids": [f"{HOST_ID}/postgresql", f"{HOST_ID}/proxy"],
            "health_signal_refs": [f"dependency/{OBSERVATION_ID}/health/reachable"],
            "source_refs": [],
        }
    ]
    payload["services"].append({"id": f"{HOST_ID}/proxy", "profile_id": "proxy"})
    payload["dependencies"].append(
        {
            "id": "database-stack-internal",
            "source_service_id": f"{HOST_ID}/postgresql",
            "target_service_id": f"{HOST_ID}/proxy",
            "target_endpoint_id": f"{HOST_ID}/proxy/http",
            "required": True,
            "execution_adapter": "gatus",
            "health_signal_refs": ["dependency/database-stack-internal/health/reachable"],
        }
    )
    plan.write_text(json.dumps(payload), encoding="utf-8")

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "service",
        logical_service_id,
        "--validate",
    )
    response = json.loads(result.output)

    assert result.exit_code == 0
    assert response["result"]["target"] == {
        "type": "service",
        "id": logical_service_id,
        "canonical_name": "database.example.com/database-stack",
    }
    assert response["result"]["declared"] == {
        "host_id": HOST_ID,
        "component_service_ids": [f"{HOST_ID}/postgresql", f"{HOST_ID}/proxy"],
        "component_count": 2,
    }
    assert response["result"]["coverage"] == {
        "required": 1,
        "bound": 1,
        "unbound": 0,
        "unsupported": 0,
        "valid": True,
    }


def test_doctor_validate_requires_an_explicit_observation_plan() -> None:
    result = _invoke("host", "database.example.com", "--validate")
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "configuration_required"
    assert payload["error"]["details"] == {"source": "observation_plan"}


def test_doctor_observation_inputs_use_environment_defaults_and_flags_override(
    tmp_path: Path,
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    alternate_plan = tmp_path / "alternate-plan.json"
    alternate_bindings = tmp_path / "alternate-bindings.yml"
    alternate_plan.write_text('{"dependencies": []}', encoding="utf-8")
    alternate_bindings.write_text("bindings: []\n", encoding="utf-8")
    environment = {
        "INFRALINK_OBSERVATION_PLAN": str(plan),
        "INFRALINK_ADAPTER_BINDINGS": str(bindings),
    }

    from_environment = CliRunner().invoke(
        cli,
        ["--output", "json", *_sources(), "doctor", "host", "database.example.com", "--validate"],
        env=environment,
    )
    with_flags = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            *_sources(),
            "doctor",
            "--observation-plan",
            str(alternate_plan),
            "--adapter-bindings",
            str(alternate_bindings),
            "host",
            "database.example.com",
            "--validate",
        ],
        env=environment,
    )

    environment_payload = json.loads(from_environment.output)
    override_payload = json.loads(with_flags.output)
    assert from_environment.exit_code == with_flags.exit_code == 0
    assert environment_payload["command"]["resolved"]["observation_plan"] == str(plan)
    assert environment_payload["command"]["resolved"]["adapter_bindings"] == str(bindings)
    assert override_payload["command"]["resolved"]["observation_plan"] == str(alternate_plan)
    assert override_payload["command"]["resolved"]["adapter_bindings"] == str(alternate_bindings)


def test_global_doctor_uses_supplied_observation_inputs(tmp_path: Path) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    result = _invoke("--observation-plan", str(plan), "--adapter-bindings", str(bindings))
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["error"]["details"] == {"source": "gatus_url"}


def test_declared_dependency_edge_never_advertises_an_invalid_topology_show_action(
    tmp_path: Path,
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "edge",
        OBSERVATION_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["error"]["details"] == {"source": "gatus_url"}


def test_doctor_uses_gatus_statuses_only_outside_declaration_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda url, token: (
            calls.append((url, token))
            or [
                {
                    "name": OBSERVATION_ID,
                    "results": [{"success": True, "timestamp": "2026-08-09T00:00:00Z"}],
                }
            ]
        ),
    )

    live = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "--gatus-url",
        "http://gatus.test",
        "edge",
        OBSERVATION_ID,
    )
    live_payload = json.loads(live.output)
    assert live.exit_code == 0
    assert calls == [("http://gatus.test", None)]
    assert live_payload["result"]["status"] == "healthy"
    assert live_payload["result"]["evidence"] == []
    assert live_payload["result"]["evidence_summary"] == [
        {
            "adapter": "gatus",
            "configured": True,
            "healthy": 1,
            "unhealthy": 0,
            "unavailable": 0,
            "unknown": 0,
            "live_observation_count": 1,
            "latest_observed_at": "2026-08-09T00:00:00Z",
        }
    ]
    verbose = live_payload["next_actions"][0]["command"]
    assert "--gatus-url http://gatus.test" in verbose
    assert "--gatus-token-env INFRALINK_GATUS_TOKEN" in verbose

    validated = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "--gatus-url",
        "http://gatus.test",
        "--validate",
        "edge",
        OBSERVATION_ID,
    )
    assert validated.exit_code == 0
    assert calls == [("http://gatus.test", None)]
    assert json.loads(validated.output)["result"]["evidence_summary"] == [
        {
            "adapter": "gatus",
            "configured": True,
            "healthy": 0,
            "unhealthy": 0,
            "unavailable": 0,
            "unknown": 1,
            "live_observation_count": 0,
            "latest_observed_at": None,
        }
    ]


def test_configured_unhealthy_required_gatus_evidence_is_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda url, token: [
            {
                "name": OBSERVATION_ID,
                "results": [{"success": False, "timestamp": "2026-08-09T00:00:00Z"}],
            }
        ],
    )
    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "--gatus-url",
        "http://gatus.test",
        "edge",
        OBSERVATION_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is True
    assert payload["result"]["status"] == "unhealthy"
    assert payload["result"]["reason"] == "gatus_observation_unhealthy"


def test_doctor_uses_the_latest_gatus_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda url, token: [
            {
                "name": OBSERVATION_ID,
                "results": [
                    {"success": False, "timestamp": "2026-08-09T00:00:00Z"},
                    {"success": True, "timestamp": "2026-08-09T00:01:00Z"},
                ],
            }
        ],
    )

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "--gatus-url",
        "http://gatus.test",
        "edge",
        OBSERVATION_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["status"] == "healthy"
    assert payload["result"]["evidence_summary"][0]["latest_observed_at"] == "2026-08-09T00:01:00Z"


def test_doctor_rejects_a_malformed_latest_gatus_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda url, token: [
            {
                "name": OBSERVATION_ID,
                "results": [{"success": True}, "malformed"],
            }
        ],
    )

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "--gatus-url",
        "http://gatus.test",
        "edge",
        OBSERVATION_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["result"]["status"] == "unknown"
    assert payload["result"]["reason"] == "no_live_observation_evidence"
    assert payload["result"]["evidence"][0]["reason"] == "gatus_result_missing"


def test_doctor_unknown_host_returns_a_bounded_canonical_discovery_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = _invoke("host", "missing-host")
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["error"]["details"] == {"source": "observation_plan"}


def test_doctor_host_includes_fail_closed_live_bootstrap_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infralink.host_readiness import HostReadinessProbe

    monkeypatch.setattr(
        "infralink.cli.doctor.SshReadinessTransport.probe",
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

    plan, bindings = _observation_inputs(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda url, token: [{"name": OBSERVATION_ID, "results": [{"success": True}]}],
    )
    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "--gatus-url",
        "http://gatus.test",
        "host",
        "database.example.com",
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["result"]["readiness"]["ready"] is False
    assert payload["result"]["readiness"]["transport"] == "root_ssh"
    assert next(
        check
        for check in payload["result"]["readiness"]["checks"]
        if check["id"] == "ssh_reachable"
    ) == {
        "id": "ssh_reachable",
        "required": True,
        "passed": True,
        "description": "Root SSH is reachable.",
        "detail": None,
    }
    plan_action = next(item for item in payload["next_actions"] if item["rel"] == "bootstrap-plan")
    assert shlex.split(plan_action["command"])[-4:] == ["host", "bootstrap", HOST_ID, "--plan"]
    assert plan_action["safe"] is True


def test_doctor_normalizes_single_host_firewall_sources_for_nft() -> None:
    from infralink.cli.doctor import _firewall_rule_lines

    assert _firewall_rule_lines(
        {"interface": "tailscale0", "sources": ["100.93.157.126/32"], "ports": [9300]},
        "tcp",
    ) == ['iifname "tailscale0" ip saddr 100.93.157.126 tcp dport 9300 accept']


def test_doctor_host_fails_closed_when_latest_v2_reconcile_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infralink.host_readiness import HostReadinessProbe

    monkeypatch.setattr(
        "infralink.cli.doctor.SshReadinessTransport.probe",
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
    plan, bindings = _observation_inputs(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda url, token: [{"name": OBSERVATION_ID, "results": [{"success": True}]}],
    )

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "--gatus-url",
        "http://gatus.test",
        "host",
        "database.example.com",
    )
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["result"]["status"] == "unhealthy"
    assert payload["result"]["reason"] == "host_readiness_incomplete"
    assert payload["result"]["readiness"]["self_deploy_reconcile_result"] == "exit-code"
    assert payload["result"]["readiness"]["self_deploy_reconcile_exit_status"] == 1
    check = next(
        check
        for check in payload["result"]["readiness"]["checks"]
        if check["id"] == "self_deploy_reconcile"
    )
    assert check["passed"] is False
    assert check["detail"] == "exit-code:1"
    verifier_action = next(item for item in payload["next_actions"] if item["rel"] == "verifier")
    assert shlex.split(verifier_action["command"])[-3:] == ["host", "verifier", HOST_ID]


def test_doctor_keeps_zero_service_provisioning_host_out_of_unhealthy_state() -> None:
    from infralink.cli.contracts import DoctorResult, DoctorTarget, HostReadinessResult
    from infralink.cli.doctor import _apply_host_readiness

    result = DoctorResult(
        target=DoctorTarget(type="host", id=HOST_ID, canonical_name="database.example.com"),
        declared={"status": "provisioning", "service_count": 0},
        evidence=[],
        evidence_summary=[],
        status="unknown",
        reason="no_live_observation_evidence",
    )
    readiness = HostReadinessResult(transport="root_ssh", ready=False, checks=[], actions=[])

    updated = _apply_host_readiness(result, readiness)

    assert updated.status == "provisioning"
    assert updated.reason == "host_provisioning_incomplete"
    assert updated.readiness == readiness


def test_doctor_reports_ready_zero_service_provisioning_host_as_provisioning() -> None:
    from infralink.cli.contracts import DoctorResult, DoctorTarget, HostReadinessResult
    from infralink.cli.doctor import _apply_host_readiness

    result = DoctorResult(
        target=DoctorTarget(type="host", id=HOST_ID, canonical_name="database.example.com"),
        declared={"status": "provisioning", "service_count": 0},
        evidence=[],
        evidence_summary=[],
        status="unknown",
        reason="no_live_observation_evidence",
    )
    readiness = HostReadinessResult(transport="root_ssh", ready=True, checks=[], actions=[])

    updated = _apply_host_readiness(result, readiness)

    assert updated.status == "provisioning"
    assert updated.reason == "host_provisioning_ready"
    assert updated.readiness == readiness


def test_doctor_fails_provisioning_host_when_its_manifest_is_not_tracked() -> None:
    from infralink.cli.contracts import DoctorResult, DoctorTarget
    from infralink.cli.doctor import _apply_host_manifest_git_state
    from infralink.host_registry_state import HostManifestGitState

    result = DoctorResult(
        target=DoctorTarget(type="host", id=HOST_ID, canonical_name="database.example.com"),
        declared={"status": "provisioning", "service_count": 0},
        evidence=[],
        evidence_summary=[],
        status="unknown",
        reason="no_live_observation_evidence",
    )
    state = HostManifestGitState(
        "local_uncommitted",
        "registry_manifest_untracked",
        Path("/registry/hosts") / HOST_ID / "manifest.yml",
        Path("/registry"),
    )

    updated = _apply_host_manifest_git_state(result, state)

    assert updated.status == "provisioning"
    assert updated.reason == "registry_manifest_untracked"
    assert updated.declared["registry_manifest"] == {
        "state": "local_uncommitted",
        "manifest_path": str(state.manifest_path),
        "git_worktree": str(state.git_worktree),
    }
