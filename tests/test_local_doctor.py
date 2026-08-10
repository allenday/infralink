from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

import pytest

from infralink.host_readiness import HostReadinessProbe
from infralink.local_doctor import (
    FirewallDeclaration,
    LatestResultStore,
    LocalDoctorCheck,
    LocalDoctorCollector,
    LocalDoctorResult,
    NftablesFirewallEvaluator,
    SshAllowedSignersTrustRoot,
    canonical_nft_rule_fingerprint,
    load_signed_firewall_declaration,
    serve_latest_result,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=timezone.utc)


def _probe(*, docker: bool = True) -> HostReadinessProbe:
    return HostReadinessProbe(
        reachable=True,
        hostname="node-1",
        machine_id="machine-id",
        commands={"git": True, "docker": docker, "tailscale": True, "jq": True, "bws": True},
        devops_account=True,
        devops_authorized_access=True,
        bws_config=True,
        self_deploy_dependencies=True,
        self_deploy_runtime=True,
        self_deploy_timer_enabled=True,
        self_deploy_timer_active=True,
        self_deploy_mode="v2_reconcile",
        self_deploy_reconcile_result="success",
        self_deploy_reconcile_exit_status=0,
        self_deploy_reconcile_active_state="inactive",
        self_deploy_reconcile_sub_state="dead",
        self_deploy_reconcile_exit_timestamp_monotonic=1,
        error="raw transport output must never be persisted",
    )


def _nft_ruleset() -> dict[str, object]:
    return {
        "nftables": [
            {
                "chain": {
                    "family": "inet",
                    "table": "infralink",
                    "name": "input",
                    "type": "filter",
                    "hook": "input",
                    "prio": 0,
                    "policy": "drop",
                }
            },
            {
                "rule": {
                    "family": "inet",
                    "table": "infralink",
                    "chain": "input",
                    "expr": [
                        {
                            "match": {
                                "op": "==",
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": 22,
                            }
                        },
                        {"accept": None},
                    ],
                }
            },
            {
                "rule": {
                    "family": "inet",
                    "table": "infralink",
                    "chain": "input",
                    "expr": [
                        {
                            "match": {
                                "op": "==",
                                "left": {"payload": {"protocol": "tcp", "field": "dport"}},
                                "right": 9200,
                            }
                        },
                        {"accept": None},
                    ],
                }
            },
        ]
    }


def _firewall_declaration() -> FirewallDeclaration:
    rules = _nft_ruleset()["nftables"]
    ssh_rule = rules[1]["rule"]  # type: ignore[index]
    ingress_rule = rules[2]["rule"]  # type: ignore[index]
    return FirewallDeclaration(
        default_deny=True,
        management_ssh_rule_fingerprint=canonical_nft_rule_fingerprint(ssh_rule),
        ingress_rule_fingerprints=frozenset({canonical_nft_rule_fingerprint(ingress_rule)}),
    )


def _competing_input_hook_ruleset(*, policy: str, prio: int) -> dict[str, object]:
    ruleset = _nft_ruleset()
    ruleset["nftables"].append(  # type: ignore[index]
        {
            "chain": {
                "family": "inet",
                "table": "competing",
                "name": "input",
                "type": "filter",
                "hook": "input",
                "prio": prio,
                "policy": policy,
            }
        }
    )
    return ruleset


def _signed_firewall_inputs(tmp_path: Path) -> tuple[Path, SshAllowedSignersTrustRoot]:
    declaration = _firewall_declaration()
    path = tmp_path / "firewall.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "infralink.local-firewall/v1",
                "declaration": {
                    "default_deny": declaration.default_deny,
                    "management_ssh_rule_fingerprint": declaration.management_ssh_rule_fingerprint,
                    "ingress_rule_fingerprints": sorted(declaration.ingress_rule_fingerprints),
                },
                "signature": "verified-signature",
            }
        ),
        encoding="utf-8",
    )
    trust_root = SshAllowedSignersTrustRoot(tmp_path / "allowed_signers")
    trust_root.allowed_signers_path.write_text("test only\n", encoding="utf-8")
    return path, trust_root


def test_collector_emits_a_stable_secret_free_result_from_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, trust_root = _signed_firewall_inputs(tmp_path)
    monkeypatch.setattr("infralink.local_doctor._verify_ssh_signature", lambda *args: True)
    result = LocalDoctorCollector(clock=lambda: NOW, freshness_seconds=60).collect(
        canonical_name="node-1",
        probe=_probe(docker=False),
        firewall_declaration_path=path,
        firewall_trust_root=trust_root,
        firewall_evaluator=NftablesFirewallEvaluator(
            command_runner=lambda argv: json.dumps(_nft_ruleset())
        ),
    )

    payload = result.to_dict()

    assert payload["schema_version"] == "infralink.local-doctor/v1"
    assert payload["status"] == "unhealthy"
    assert payload["observed_at"] == "2026-08-09T12:00:00Z"
    assert payload["fresh_until"] == "2026-08-09T12:01:00Z"
    assert {item["id"] for item in payload["checks"]} >= {"docker", "self_deploy_reconcile"}
    firewall = next(item for item in payload["checks"] if item["id"] == "firewall_converged")
    assert firewall == {
        "id": "firewall_converged",
        "required": True,
        "passed": True,
        "severity": "info",
        "detail_code": "ok",
    }
    assert "raw transport output" not in json.dumps(payload)
    assert "machine-id" not in json.dumps(payload)


def test_latest_result_store_replaces_the_previous_result_atomically(tmp_path: Path) -> None:
    path = tmp_path / "doctor" / "latest.json"
    store = LatestResultStore(path)
    healthy = LocalDoctorResult.healthy(now=NOW, freshness_seconds=60)
    unhealthy = LocalDoctorResult.unhealthy(now=NOW, freshness_seconds=60)

    store.write(healthy)
    store.write(unhealthy)

    assert store.load() == unhealthy
    assert json.loads(path.read_text(encoding="utf-8"))["status"] == "unhealthy"
    assert not list(path.parent.glob(".latest.json.*"))


@pytest.mark.parametrize("check_id", ["contains a space", "token=secret", "x" * 65])
def test_result_rejects_unbounded_check_identifiers(check_id: str) -> None:
    with pytest.raises(ValueError, match="check id"):
        LocalDoctorCheck(id=check_id, required=True, passed=True)


def test_result_rejects_duplicate_check_identifiers() -> None:
    check = LocalDoctorCheck(id="docker", required=True, passed=True)

    with pytest.raises(ValueError, match="duplicate"):
        LocalDoctorResult(NOW, NOW + timedelta(seconds=60), "healthy", (check, check))


def test_result_rejects_unbounded_finding_detail_codes() -> None:
    with pytest.raises(ValueError, match="detail code"):
        LocalDoctorCheck(
            id="docker",
            required=True,
            passed=False,
            severity="error",
            detail_code="iptables -S contains 100.64.68.83",
        )


def test_result_rejects_detail_code_not_allowed_for_that_check() -> None:
    with pytest.raises(ValueError, match="detail code"):
        LocalDoctorCheck(
            id="docker",
            required=True,
            passed=False,
            severity="error",
            detail_code="firewall_not_converged",
        )


def test_legacy_v1_result_loads_deterministic_typed_check_defaults() -> None:
    result = LocalDoctorResult.from_dict(
        {
            "schema_version": "infralink.local-doctor/v1",
            "observed_at": "2026-08-09T12:00:00Z",
            "fresh_until": "2026-08-09T12:01:00Z",
            "status": "healthy",
            "checks": [{"id": "docker", "required": True, "passed": True}],
        }
    )

    assert result.checks[0].to_dict() == {
        "id": "docker",
        "required": True,
        "passed": True,
        "severity": "info",
        "detail_code": "ok",
    }


def test_legacy_v1_unknown_check_ids_map_to_one_bounded_compatibility_check() -> None:
    result = LocalDoctorResult.from_dict(
        {
            "schema_version": "infralink.local-doctor/v1",
            "observed_at": "2026-08-09T12:00:00Z",
            "fresh_until": "2026-08-09T12:01:00Z",
            "status": "healthy",
            "checks": [
                {"id": "old-custom-check", "required": True, "passed": True},
                {"id": "arbitrary-prior-id", "required": False, "passed": True},
            ],
        }
    )

    assert [check.to_dict() for check in result.checks] == [
        {
            "id": "legacy_compatibility",
            "required": True,
            "passed": True,
            "severity": "info",
            "detail_code": "ok",
        }
    ]


def test_signed_firewall_declaration_requires_signature_before_local_evaluation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    declaration = _firewall_declaration()
    path, trust_root = _signed_firewall_inputs(tmp_path)
    monkeypatch.setattr("infralink.local_doctor._verify_ssh_signature", lambda *args: True)
    loaded = load_signed_firewall_declaration(path, trust_root=trust_root)

    assert loaded == declaration
    with pytest.raises(ValueError, match="signature"):
        monkeypatch.setattr("infralink.local_doctor._verify_ssh_signature", lambda *args: False)
        load_signed_firewall_declaration(path, trust_root=trust_root)

    monkeypatch.setattr("infralink.local_doctor._verify_ssh_signature", lambda *args: True)
    result = LocalDoctorCollector(clock=lambda: NOW, freshness_seconds=60).collect(
        canonical_name="node-1",
        probe=replace(_probe(), registry_layout="v2_managed"),
        firewall_declaration_path=path,
        firewall_trust_root=trust_root,
        firewall_evaluator=NftablesFirewallEvaluator(
            command_runner=lambda argv: json.dumps(_nft_ruleset())
        ),
    )
    assert result.status == "healthy"


def test_collector_uses_live_nftables_evaluation_for_required_firewall_checks() -> None:
    result = NftablesFirewallEvaluator(
        command_runner=lambda argv: json.dumps(_nft_ruleset())
    ).evaluate(_firewall_declaration())

    assert {check.id for check in result} == {
        "firewall_default_deny",
        "firewall_management_ssh",
        "firewall_declared_ingress",
        "firewall_converged",
    }


def test_firewall_evaluator_flags_unexpected_live_ingress_without_exposing_rules() -> None:
    result = NftablesFirewallEvaluator(
        command_runner=lambda argv: json.dumps(_nft_ruleset())
    ).evaluate(
        FirewallDeclaration(
            default_deny=True,
            management_ssh_rule_fingerprint=_firewall_declaration().management_ssh_rule_fingerprint,
            ingress_rule_fingerprints=frozenset(),
        )
    )

    ingress = next(check for check in result if check.id == "firewall_declared_ingress")
    assert ingress.to_dict() == {
        "id": "firewall_declared_ingress",
        "required": True,
        "passed": False,
        "severity": "error",
        "detail_code": "firewall_ingress_mismatch",
    }


@pytest.mark.parametrize("policy,prio", [("accept", -100), ("drop", 100)])
def test_firewall_evaluator_fails_closed_for_any_competing_input_hook(
    policy: str, prio: int
) -> None:
    result = NftablesFirewallEvaluator(
        command_runner=lambda argv: json.dumps(
            _competing_input_hook_ruleset(policy=policy, prio=prio)
        )
    ).evaluate(_firewall_declaration())

    aggregate = next(check for check in result if check.id == "firewall_converged")
    assert aggregate.to_dict() == {
        "id": "firewall_converged",
        "required": True,
        "passed": False,
        "severity": "error",
        "detail_code": "firewall_competing_input_hook",
    }


def test_result_rejects_healthy_status_with_a_failed_required_check() -> None:
    with pytest.raises(ValueError, match="healthy"):
        LocalDoctorResult(
            NOW,
            NOW + timedelta(seconds=60),
            "healthy",
            (LocalDoctorCheck(id="docker", required=True, passed=False),),
        )


def test_result_rejects_naive_timestamps() -> None:
    naive = datetime(2026, 8, 9, 12, 0)

    with pytest.raises(ValueError, match="timezone"):
        LocalDoctorResult(naive, naive + timedelta(seconds=60), "healthy", ())


def test_freshness_expires_at_its_declared_cutoff() -> None:
    result = LocalDoctorResult.healthy(now=NOW, freshness_seconds=60)

    assert result.is_fresh_healthy(now=NOW + timedelta(seconds=59))
    assert not result.is_fresh_healthy(now=NOW + timedelta(seconds=60))


def test_latest_result_store_syncs_its_parent_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import infralink.local_doctor as local_doctor

    calls: list[int] = []
    real_fsync = local_doctor.os.fsync
    monkeypatch.setattr(
        local_doctor.os,
        "fsync",
        lambda descriptor: (calls.append(descriptor), real_fsync(descriptor))[1],
    )

    LatestResultStore(tmp_path / "latest.json").write(
        LocalDoctorResult.healthy(now=NOW, freshness_seconds=60)
    )

    assert len(calls) == 2


@pytest.mark.parametrize(
    ("result", "now", "expected_status"),
    [
        (None, NOW, 503),
        (LocalDoctorResult.healthy(now=NOW, freshness_seconds=60), NOW, 200),
        (LocalDoctorResult.unhealthy(now=NOW, freshness_seconds=60), NOW, 503),
        (
            LocalDoctorResult.healthy(now=NOW, freshness_seconds=60),
            NOW + timedelta(seconds=61),
            503,
        ),
    ],
)
def test_static_endpoint_reports_only_fresh_healthy_latest_result(
    tmp_path: Path, result: LocalDoctorResult | None, now: datetime, expected_status: int
) -> None:
    store = LatestResultStore(tmp_path / "latest.json")
    if result is not None:
        store.write(result)
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: now)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/doctor/latest"
        if expected_status == 200:
            with urlopen(url, timeout=2) as response:  # noqa: S310 - local test server
                assert response.status == 200
                assert json.loads(response.read())["status"] == "healthy"
        else:
            with pytest.raises(HTTPError) as error:
                urlopen(url, timeout=2)  # noqa: S310 - local test server
            assert error.value.code == 503
            payload = json.loads(error.value.read())
            assert payload["schema_version"] == "infralink.local-doctor/v1"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_static_endpoint_never_runs_the_collector_for_requests(tmp_path: Path) -> None:
    store = LatestResultStore(tmp_path / "latest.json")
    store.write(LocalDoctorResult.healthy(now=NOW, freshness_seconds=60))
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/v1/doctor/latest", timeout=2
        ) as response:  # noqa: S310 - local test server
            assert response.status == 200
        with pytest.raises(HTTPError) as error:
            urlopen(f"http://127.0.0.1:{server.server_port}/not-found", timeout=2)  # noqa: S310 - local test server
        assert error.value.code == 404
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_static_endpoint_serves_legacy_v1_persisted_results(tmp_path: Path) -> None:
    store = LatestResultStore(tmp_path / "latest.json")
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text(
        json.dumps(
            {
                "schema_version": "infralink.local-doctor/v1",
                "observed_at": "2026-08-09T12:00:00Z",
                "fresh_until": "2026-08-09T12:01:00Z",
                "status": "healthy",
                "checks": [{"id": "docker", "required": True, "passed": True}],
            }
        ),
        encoding="utf-8",
    )
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(
            f"http://127.0.0.1:{server.server_port}/v1/doctor/latest", timeout=2
        ) as response:  # noqa: S310 - local test server
            payload = json.loads(response.read())
        assert response.status == 200
        assert payload["checks"][0]["detail_code"] == "ok"
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_metrics_endpoint_exposes_only_bounded_typed_doctor_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, trust_root = _signed_firewall_inputs(tmp_path)
    monkeypatch.setattr("infralink.local_doctor._verify_ssh_signature", lambda *args: True)
    store = LatestResultStore(tmp_path / "latest.json")
    store.write(
        LocalDoctorCollector(clock=lambda: NOW, freshness_seconds=60).collect(
            canonical_name="node-1",
            probe=_probe(),
            firewall_declaration_path=path,
            firewall_trust_root=trust_root,
            firewall_evaluator=NftablesFirewallEvaluator(
                command_runner=lambda argv: json.dumps(_nft_ruleset())
            ),
        )
    )
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/metrics", timeout=2) as response:
            metrics = response.read().decode()
        assert response.status == 200
        assert 'infralink_local_doctor_status{status="unhealthy"} 1' in metrics
        assert "infralink_local_doctor_fresh 1" in metrics
        assert (
            'infralink_local_doctor_check{check_id="firewall_converged",'
            'required="true",severity="info",detail_code="ok"} 1'
        ) in metrics
        assert "machine-id" not in metrics
        assert "100.64" not in metrics
        assert "/var/lib" not in metrics
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_metrics_marks_expired_healthy_evidence_not_converged(tmp_path: Path) -> None:
    store = LatestResultStore(tmp_path / "latest.json")
    store.write(LocalDoctorResult.healthy(now=NOW, freshness_seconds=60))
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: NOW + timedelta(seconds=60))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urlopen(f"http://127.0.0.1:{server.server_port}/metrics", timeout=2) as response:
            metrics = response.read().decode()
        assert "infralink_local_doctor_converged 0" in metrics
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_static_endpoint_fails_closed_for_contradictory_persisted_result(tmp_path: Path) -> None:
    store = LatestResultStore(tmp_path / "latest.json")
    payload = LocalDoctorResult.healthy(now=NOW, freshness_seconds=60).to_dict()
    payload["checks"] = [{"id": "docker", "required": True, "passed": False}]
    store.path.write_text(json.dumps(payload), encoding="utf-8")
    server = serve_latest_result("127.0.0.1", 0, store, clock=lambda: NOW)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(HTTPError) as error:
            urlopen(f"http://127.0.0.1:{server.server_port}/v1/doctor/latest", timeout=2)  # noqa: S310 - local test server
        assert error.value.code == 503
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()
