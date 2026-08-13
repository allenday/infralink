from __future__ import annotations

import subprocess
from contextlib import nullcontext
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.main import cli
from infralink.cli.operations import OperationRecord
from tests.cli_helpers import assert_schema

HOST_ID = "32a3324f-c3d0-4a4f-9587-52c099bcb3fb"
HOST_NAME = "relaxgg-db-es1"
UNIT = "infralink-host-reconcile.service"
INVOCATION = "8d6c4ad60e4a4b589fe35ad9e1760d56"
FINGERPRINT = "SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
MANIFEST_FINGERPRINT = "SHA256:Pnpjf51QfL7khY8GiWuWNp/5G9Twt321Dd5Dk8dB50w"
TARGET_HOST_FINGERPRINT = "SHA256:KdpS7oRVMZ2t0JRHDp/K6xEqQoiZeHuVlDn/gG5veFA"
OBSERVED_FINGERPRINTS = (
    "SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
    "SHA256:CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
)
CORE_HOSTS = (
    ("9157ddeb-cb6d-4d55-8252-9db358f5d932", "cyberstorm-citadel", "100.73.228.90"),
    ("7ffe46b7-0eb4-40cb-8e14-ea679b9948f4", "cyberstorm-watchtower", "100.123.0.63"),
    (HOST_ID, HOST_NAME, "100.64.68.83"),
)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _registry_checkout(tmp_path: Path, *, declared: bool = True) -> Path:
    root = tmp_path / "registry"
    host = root / "hosts" / HOST_ID
    host.mkdir(parents=True)
    (host / "manifest.yml").write_text(
        "hosts:\n"
        f"  {HOST_ID}:\n"
        f"    canonical_name: {HOST_NAME}\n"
        "    status: active\n"
        "    tailscale_ip: 100.64.68.83\n",
        encoding="utf-8",
    )
    if declared:
        operations = host / "operations"
        operations.mkdir()
        (operations / "contract.yml").write_text(
            "schema_version: host-operations-contract.v1\n"
            "machine:\n"
            f"  uuid: {HOST_ID}\n"
            f"  canonical_name: {HOST_NAME}\n"
            "transport:\n"
            "  kind: ssh\n"
            "  host: 100.64.68.83\n"
            "  port: 22\n"
            "  user: root\n"
            f"  host_key_fingerprint: {FINGERPRINT}\n"
            "reconcile:\n"
            f"  unit: {UNIT}\n",
            encoding="utf-8",
        )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "registry")
    return root / "hosts"


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _manifest_registry_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "registry"
    for host_id, canonical_name, address in CORE_HOSTS:
        host = root / "hosts" / host_id
        host.mkdir(parents=True)
        (host / "manifest.yml").write_text(
            "hosts:\n"
            f"  {host_id}:\n"
            f"    canonical_name: {canonical_name}\n"
            "    status: active\n"
            f"    tailscale_ip: {address}\n"
            f"    self_deploy_v2_promotion_host_fingerprint: ssh-rsa {MANIFEST_FINGERPRINT}\n"
            f"    self_deploy_v2_target_ssh_host_fingerprint: ssh-rsa {TARGET_HOST_FINGERPRINT}\n"
            "    self_deploy_v2_promotion_channel: core-v2\n"
            "    self_deploy_v2_promotion_policy_enabled: true\n"
            "    self_deploy_v2_reconcile_enabled: true\n"
            "    self_deploy_v2_reconcile_packaged: true\n",
            encoding="utf-8",
        )
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "manifest registry")
    return root / "hosts"


def test_manifest_v2_apply_uses_the_declared_target_host_fingerprint(tmp_path: Path) -> None:
    """Registry server trust must never double as the target SSH transport key."""
    from infralink.cli.operations import resolve_apply_request

    registry = _manifest_registry_checkout(tmp_path)
    target = type(
        "Target",
        (),
        {"uuid": HOST_ID, "canonical_name": HOST_NAME},
    )()

    request = resolve_apply_request(registry, target)

    assert request.host_key_fingerprint == TARGET_HOST_FINGERPRINT


def test_manifest_v2_apply_retains_legacy_transport_fingerprint_until_migrated(
    tmp_path: Path,
) -> None:
    """Existing V2 hosts stay operable until their declarations are migrated."""
    from infralink.cli.operations import resolve_apply_request

    registry = _manifest_registry_checkout(tmp_path)
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            f"    self_deploy_v2_target_ssh_host_fingerprint: ssh-rsa {TARGET_HOST_FINGERPRINT}\n",
            "",
        ),
        encoding="utf-8",
    )
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "legacy V2 manifest")
    target = type("Target", (), {"uuid": HOST_ID, "canonical_name": HOST_NAME})()

    request = resolve_apply_request(registry, target)

    assert request.host_key_fingerprint == MANIFEST_FINGERPRINT


def _release_admission_layout(registry: Path, host_id: str = HOST_ID) -> None:
    operations = registry / host_id / "operations"
    operations.mkdir(exist_ok=True)
    (operations / "release-admission-shadow-source.yml").write_text(
        "schema_version: infralink.release-admission-shadow-delivery.v1\n"
        "registry:\n"
        "  remote: ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
        "  repository: /var/lib/release-admission/registry-objects/citadel\n"
        "paths:\n"
        "  runtime_root: /var/lib/release-admission/runtime/" + "a" * 40 + "\n"
        "release:\n"
        "  allowed_signers_file: /etc/infralink/release-admission/allowed_signers\n",
        encoding="utf-8",
    )


def _explicit_legacy_verifier_layout(registry: Path) -> None:
    contract = registry / HOST_ID / "operations" / "contract.yml"
    contract.write_text(
        contract.read_text(encoding="utf-8") + "verifier:\n"
        "  registry:\n"
        "    remote: ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
        "    repository: /var/lib/legacy/registry\n"
        "    ref: refs/heads/main\n"
        "  runtime_root: /var/lib/legacy/runtime/" + "a" * 40 + "\n"
        "  allowed_signers_file: /etc/legacy/allowed_signers\n",
        encoding="utf-8",
    )


@pytest.mark.parametrize(("host_id", "canonical_name", "address"), CORE_HOSTS)
def test_host_apply_dry_run_derives_each_core_transport_from_its_manifest(
    tmp_path: Path, host_id: str, canonical_name: str, address: str
) -> None:
    registry = _manifest_registry_checkout(tmp_path)

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", host_id, "--dry-run"]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert payload["result"] == {
        "dry_run": True,
        "target": {"type": "host", "id": host_id, "canonical_name": canonical_name},
        "plan": {
            "registry_revision": _git(registry.parent, "rev-parse", "HEAD"),
            "dispatch_provider": "ssh",
            "reconcile_mode": "timer",
            "action_categories": ["registry_checkout", "render", "reconcile"],
        },
    }


def test_host_verifier_uses_explicit_legacy_layout_without_path_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    _explicit_legacy_verifier_layout(registry)
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "explicit legacy verifier layout")
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _completed(
            "registry_remote=ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
            "registry_ref=refs/heads/main\n"
            "runtime_revision=" + "a" * 40 + "\n"
            "allowed_signer_principal=infra\n"
            "allowed_signer_fingerprint=SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\n"
            "allowed_signers_sha256=" + "c" * 64 + "\n"
            "git_ssh_signature_capable=true\n"
            "fetched_tip=" + "d" * 40 + "\n"
            "signature_verification=passed\n"
        )

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "verifier", HOST_ID])

    assert response.exit_code == 0
    assert calls[0][-6:] == [
        "verifier",
        "/var/lib/legacy/registry",
        "/var/lib/legacy/runtime/" + "a" * 40,
        "/etc/legacy/allowed_signers",
        "ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git",
        "refs/heads/main",
    ]


def test_host_verifier_marks_controller_hosts_unavailable_without_running_stale_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _manifest_registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: pytest.fail("retired verifier must not open SSH"),
    )

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "verifier", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 1
    assert_schema(payload, "host-verifier")
    assert payload["result"]["verifier"]["unavailable"] == [
        "registry_remote",
        "registry_ref",
        "runtime_revision",
        "allowed_signer",
        "git_ssh_signature_capable",
        "fetched_tip",
        "signature_verification",
    ]


def test_host_apply_reports_only_normalized_observed_fingerprints_on_key_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    calls: list[list[str]] = []

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "ssh-keyscan":
            return _completed(
                "100.64.68.83 ssh-ed25519 opaque-public-key-two\n"
                "100.64.68.83 ssh-rsa opaque-public-key-one\n"
                "100.64.68.83 ssh-ed25519 opaque-public-key-two\n"
            )
        if args[0] == "ssh-keygen":
            key_line = str(kwargs["input"])
            fingerprint = (
                OBSERVED_FINGERPRINTS[0]
                if "opaque-public-key-one" in key_line
                else OBSERVED_FINGERPRINTS[1]
            )
            return _completed(f"256 {fingerprint} scanned-host (ED25519)\n")
        raise AssertionError("host apply must not open an SSH session after a key mismatch")

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"] == {
        "code": "provider_unavailable",
        "message": "Declared host SSH key does not match its fingerprint",
        "details": {"observed_fingerprints": list(OBSERVED_FINGERPRINTS)},
    }
    assert "opaque-public-key" not in response.output
    assert [call[0] for call in calls] == ["ssh-keyscan", "ssh-keygen", "ssh-keygen", "ssh-keygen"]


def test_host_apply_rejects_a_nonzero_fingerprint_scan_even_when_stdout_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "ssh-keyscan":
            return _completed("100.64.68.83 ssh-rsa opaque-public-key\n")
        if args[0] == "ssh-keygen":
            return subprocess.CompletedProcess(
                args=[], returncode=1, stdout=f"256 {FINGERPRINT} scanned-host (RSA)\n", stderr=""
            )
        raise AssertionError(
            "host apply must not open an SSH session after fingerprint validation fails"
        )

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "provider_unavailable"
    assert payload["error"]["details"] == {"observed_fingerprints": []}
    assert "opaque-public-key" not in response.output
    assert [call[0] for call in calls] == ["ssh-keyscan", "ssh-keygen"]


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tailscale_ip", "not an address"),
        ("tailscale_ip", "192.0.2.1"),
        ("self_deploy_v2_target_ssh_host_fingerprint", "ssh-rsa SHA256:short"),
        ("self_deploy_v2_promotion_channel", "not valid"),
        ("self_deploy_v2_reconcile_enabled", "false"),
        ("self_deploy_v2_reconcile_packaged", "false"),
    ),
)
def test_host_apply_refuses_malformed_or_disabled_manifest_declarations(
    tmp_path: Path, field: str, value: str
) -> None:
    registry = _manifest_registry_checkout(tmp_path)
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            {
                "tailscale_ip": "tailscale_ip: 100.64.68.83",
                "self_deploy_v2_promotion_host_fingerprint": (
                    f"self_deploy_v2_promotion_host_fingerprint: ssh-rsa {MANIFEST_FINGERPRINT}"
                ),
                "self_deploy_v2_target_ssh_host_fingerprint": (
                    f"self_deploy_v2_target_ssh_host_fingerprint: ssh-rsa {TARGET_HOST_FINGERPRINT}"
                ),
                "self_deploy_v2_promotion_channel": "self_deploy_v2_promotion_channel: core-v2",
                "self_deploy_v2_reconcile_enabled": "self_deploy_v2_reconcile_enabled: true",
                "self_deploy_v2_reconcile_packaged": "self_deploy_v2_reconcile_packaged: true",
            }[field],
            f"{field}: {value}",
        ),
        encoding="utf-8",
    )
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "malformed manifest")

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", HOST_ID, "--dry-run"]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"


def test_host_apply_refuses_a_partial_v2_manifest_instead_of_using_legacy_contract(
    tmp_path: Path,
) -> None:
    registry = _registry_checkout(tmp_path)
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "    self_deploy_v2_reconcile_packaged: true\n",
        encoding="utf-8",
    )
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "partial v2 manifest")

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", HOST_ID, "--dry-run"]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"


def test_host_verifier_uses_legacy_contract_for_a_shadow_only_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    manifest = registry / HOST_ID / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "    self_deploy_v2_shadow_enabled: true\n",
        encoding="utf-8",
    )
    contract = registry / HOST_ID / "operations" / "contract.yml"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(f"reconcile:\n  unit: {UNIT}\n", ""),
        encoding="utf-8",
    )
    _explicit_legacy_verifier_layout(registry)
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "legacy shadow contract")

    calls: list[list[str]] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _completed(
            "registry_remote=ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git\n"
            "registry_ref=refs/heads/main\n"
            "runtime_revision=" + "a" * 40 + "\n"
            "allowed_signer_principal=infra\n"
            "allowed_signer_fingerprint=SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\n"
            "allowed_signers_sha256=" + "c" * 64 + "\n"
            "git_ssh_signature_capable=true\n"
            "fetched_tip=" + "d" * 40 + "\n"
            "signature_verification=passed\n"
        )

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "verifier", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-verifier")
    assert calls[0][calls[0].index("--") + 1] == "verifier"


def test_host_apply_rejects_a_legacy_contract_with_a_different_reconcile_unit(
    tmp_path: Path,
) -> None:
    registry = _registry_checkout(tmp_path)
    contract = registry / HOST_ID / "operations" / "contract.yml"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(UNIT, "not-the-supported-unit.service"),
        encoding="utf-8",
    )
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "invalid legacy unit")

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", HOST_ID, "--dry-run"]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"
    assert "reconcile unit" in payload["error"]["message"].lower()


def test_host_apply_starts_only_declared_reconcile_unit_and_returns_opaque_run_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    calls: list[list[str]] = []
    fingerprints: list[str] = []

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _completed(
            f"InvocationID={INVOCATION}\nActiveState=activating\nResult=success\nExecMainStatus=0\n"
        )

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: (
            fingerprints.append(request.host_key_fingerprint),
            nullcontext(Path("/tmp/known-hosts")),
        )[1],
    )
    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert calls == [
        [
            "ssh",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "UserKnownHostsFile=/tmp/known-hosts",
            "-p",
            "22",
            "root@100.64.68.83",
            "sh",
            "-s",
            "--",
            UNIT,
        ]
    ]
    assert fingerprints == [FINGERPRINT]
    assert payload["result"] == {
        "operation": {
            "id": f"ssh/{HOST_ID}/{INVOCATION}",
            "state": "applying",
        },
        "target": {"type": "host", "id": HOST_ID, "canonical_name": HOST_NAME},
        "dispatch": {"provider": "ssh", "status": "accepted"},
    }
    assert payload["next_actions"][0]["rel"] == "status"


def test_host_apply_wait_polls_exact_run_until_converged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    responses = iter(
        [
            _completed(
                f"InvocationID={INVOCATION}\n"
                "ActiveState=activating\nResult=success\nExecMainStatus=0\n"
            ),
            _completed(
                f"InvocationID={INVOCATION}\n"
                "ActiveState=inactive\nResult=success\nExecMainStatus=0\n"
            ),
        ]
    )
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run", lambda *args, **kwargs: next(responses)
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    monkeypatch.setattr("infralink.cli.operations.time.sleep", lambda _: None)

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", HOST_ID, "--wait", "--timeout", "1"]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert payload["result"]["operation"]["state"] == "converged"
    assert payload["next_actions"][0]["rel"] == "doctor"


def test_host_apply_refuses_a_host_without_declared_ssh_reconcile_contract(tmp_path: Path) -> None:
    registry = _registry_checkout(tmp_path, declared=False)

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"
    assert "declared" in payload["error"]["message"].lower()


def test_host_status_reads_target_timer_and_last_reconcile_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            "timer_active=active\n"
            "timer_next=2026-08-13T17:00:00Z\n"
            "unit_active=inactive\n"
            "unit_result=success\n"
            "unit_status=0\n"
            "registry_sha=" + "a" * 40 + "\n"
            "finished_at=2026-08-13T16:00:00Z\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "status", HOST_ID])
    payload = yaml.safe_load(response.output)

    assert response.exit_code == 0
    assert payload["result"] == {
        "target": {"type": "host", "id": HOST_ID, "canonical_name": HOST_NAME},
        "reconcile_mode": "timer",
        "timer": {"active": True, "next_scheduled_at": "2026-08-13T17:00:00Z"},
        "in_progress": False,
        "last_reconcile": {
            "status": "success",
            "registry_sha": "a" * 40,
            "finished_at": "2026-08-13T16:00:00Z",
        },
    }
    assert_schema(payload, "host-status")


def test_host_apply_reports_healthy_target_timer_when_direct_dispatch_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)

    class UnavailableProvider:
        def submit(self, request: object) -> OperationRecord:
            raise CliFailure(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Declared host SSH reconcile operation is unavailable",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Retry",
                details={"dispatch": "unavailable"},
            )

    monkeypatch.setattr(
        "infralink.cli.operations.operation_provider", lambda: UnavailableProvider()
    )
    monkeypatch.setattr(
        "infralink.cli.operations.inspect_target_status",
        lambda request: {
            "timer_active": "active",
            "timer_next": "2026-08-13T17:00:00Z",
            "unit_active": "inactive",
            "unit_result": "success",
            "registry_sha": "b" * 40,
            "finished_at": "2026-08-13T16:00:00Z",
        },
    )

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", HOST_ID, "--wait"]
    )
    payload = yaml.safe_load(response.output)

    assert response.exit_code == 0
    assert_schema(payload, "host-apply")
    assert payload["result"] == {
        "target": {"type": "host", "id": HOST_ID, "canonical_name": HOST_NAME},
        "dispatch": {"provider": "ssh", "status": "unavailable"},
        "target_status": {
            "reconcile_mode": "timer",
            "timer": {"active": True, "next_scheduled_at": "2026-08-13T17:00:00Z"},
            "in_progress": False,
            "last_reconcile": {
                "status": "success",
                "registry_sha": "b" * 40,
                "finished_at": "2026-08-13T16:00:00Z",
            },
        },
    }
    assert {item["rel"] for item in payload["next_actions"]} == {"status", "logs"}


def test_host_logs_last_run_returns_bounded_sanitized_target_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            '{"ok":false,"error_code":"reconcile_render_failed",'
            '"error_stage":"apply","retryable":true,"secret":"not-output"}\n'
            "unstructured line\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "logs", HOST_ID, "--last-run"]
    )
    payload = yaml.safe_load(response.output)

    assert response.exit_code == 0
    assert payload["result"] == {
        "target": {"type": "host", "id": HOST_ID, "canonical_name": HOST_NAME},
        "lines": [
            "code: reconcile_render_failed",
            "stage: apply",
            "retryable: true",
        ],
    }
    assert "not-output" not in response.output
    assert_schema(payload, "host-logs")


def test_host_status_marks_an_active_target_reconcile_in_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            "timer_active=active\n"
            "timer_next=2026-08-13T17:00:00Z\n"
            "unit_active=activating\n"
            "unit_result=success\n"
            "registry_sha=" + "a" * 40 + "\n"
            "finished_at=2026-08-13T16:00:00Z\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "status", HOST_ID])
    payload = yaml.safe_load(response.output)

    assert response.exit_code == 0
    assert payload["result"]["in_progress"] is True


def test_host_status_drops_an_untrusted_finished_timestamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            "timer_active=active\nunit_active=inactive\nunit_result=success\n"
            "registry_sha=" + "a" * 40 + "\n"
            "finished_at=repository-wide-loaded-secret-value-canary\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "status", HOST_ID])

    assert response.exit_code == 0
    assert "repository-wide-loaded-secret-value-canary" not in response.output
    assert yaml.safe_load(response.output)["result"]["last_reconcile"]["finished_at"] is None


def test_host_help_discovers_target_status_and_last_run_logs() -> None:
    result = CliRunner().invoke(cli, ["help", "host"])
    payload = yaml.safe_load(result.output)

    assert result.exit_code == 0
    children = {item["name"] for item in payload["result"]["children"]}
    assert {"status", "logs"} <= children


def test_host_apply_refuses_a_noncanonical_ssh_fingerprint(tmp_path: Path) -> None:
    registry = _registry_checkout(tmp_path)
    contract = registry / HOST_ID / "operations" / "contract.yml"
    contract.write_text(
        contract.read_text(encoding="utf-8").replace(FINGERPRINT, "SHA256:short"), encoding="utf-8"
    )
    _git(registry.parent, "add", ".")
    _git(registry.parent, "commit", "--quiet", "-m", "invalid fingerprint")

    response = CliRunner().invoke(cli, ["--registry", str(registry), "host", "apply", HOST_ID])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert "fingerprint" in payload["error"]["message"].lower()


def test_operation_status_queries_the_declared_host_local_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            f"InvocationID={INVOCATION}\nActiveState=inactive\nResult=success\nExecMainStatus=0\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    operation_id = f"ssh/{HOST_ID}/{INVOCATION}"

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "operation", "status", operation_id]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert_schema(payload, "operation-status")
    assert payload["result"]["operation"] == {"id": operation_id, "state": "converged"}
    assert payload["result"]["target"] == {
        "type": "host",
        "id": HOST_ID,
        "canonical_name": HOST_NAME,
    }


def test_operation_status_reads_a_terminal_result_from_the_host_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            "InvocationID=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\n"
            "ActiveState=inactive\nResult=success\nExecMainStatus=0\n"
            '{"ok":true,"run_id":"durable-host-local-result"}\n'
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    operation_id = f"ssh/{HOST_ID}/{INVOCATION}"

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "operation", "status", operation_id]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert payload["result"]["operation"] == {"id": operation_id, "state": "converged"}


def test_operation_status_reports_bounded_sanitized_terminal_failure_diagnostics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            f"InvocationID={INVOCATION}\n"
            "ActiveState=inactive\n"
            "Result=exit-code\n"
            "ExecMainStatus=1\n"
            "__INFRALINK_JOURNAL__\n"
            '{"ok":false,"error_code":"reconcile_launcher_process_failed",'
            '"error_stage":"apply","retryable":true,"token":"super-secret-value"}\n'
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    operation_id = f"ssh/{HOST_ID}/{INVOCATION}"

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "operation", "status", operation_id]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 1
    assert_schema(payload, "operation-status")
    assert payload["result"]["operation"] == {"id": operation_id, "state": "failed"}
    assert payload["result"]["failure"] == {
        "unit": {"active_state": "inactive", "result": "exit-code", "exec_main_status": 1},
        "journal": [
            "code: reconcile_launcher_process_failed",
            "stage: apply",
            "retryable: true",
        ],
    }
    assert "super-secret-value" not in response.output


def test_host_apply_wait_projects_the_same_canonical_failure_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    responses = iter(
        [
            _completed(
                f"InvocationID={INVOCATION}\n"
                "ActiveState=activating\n"
                "Result=success\n"
                "ExecMainStatus=0\n"
            ),
            _completed(
                f"InvocationID={INVOCATION}\n"
                "ActiveState=failed\n"
                "Result=exit-code\n"
                "ExecMainStatus=1\n"
                "__INFRALINK_JOURNAL__\n"
                '{"ok":false,"error_code":"reconcile_launcher_process_failed",'
                '"error_stage":"apply","retryable":true,"secret":"not-output"}\n'
            ),
        ]
    )
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run", lambda *args, **kwargs: next(responses)
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    monkeypatch.setattr("infralink.cli.operations.time.sleep", lambda _: None)

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "host", "apply", HOST_ID, "--wait", "--timeout", "1"]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 1
    assert_schema(payload, "host-apply")
    assert payload["result"]["failure"]["journal"] == [
        "code: reconcile_launcher_process_failed",
        "stage: apply",
        "retryable: true",
    ]
    assert "not-output" not in response.output


@pytest.mark.parametrize(
    "journal",
    [
        '{"ok":true,"code":"successful_but_irrelevant"}',
        '{"code":"unscoped_legacy_code"}',
        '{"ok":false,"code":"legacy_code_without_canonical_error_code"}',
        '{"ok":false,"error_code":"missing_stage_and_retryable"}',
        '{"ok":false,"error_code":"invalid_stage","error_stage":"unsafe","retryable":true}',
        '{"ok":false,"error_code":"invalid_retryable","error_stage":"apply","retryable":"true"}',
    ],
)
def test_operation_status_omits_noncanonical_journal_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, journal: str
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            f"InvocationID={INVOCATION}\n"
            "ActiveState=inactive\n"
            "Result=exit-code\n"
            "ExecMainStatus=1\n"
            "__INFRALINK_JOURNAL__\n"
            f"{journal}\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "operation", "status", f"ssh/{HOST_ID}/{INVOCATION}"],
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 1
    assert payload["result"]["failure"]["journal"] == ["unstructured journal output omitted"]
    assert "code:" not in response.output


def test_operation_status_never_truncates_a_canonical_failure_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    journal = "\n".join(
        (
            '{"ok":false,"error_code":"first_failure","error_stage":"inspect","retryable":false}',
            '{"ok":false,"error_code":"second_failure","error_stage":"apply","retryable":true}',
            '{"ok":false,"error_code":"third_failure","error_stage":"record","retryable":false}',
        )
    )
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            f"InvocationID={INVOCATION}\n"
            "ActiveState=inactive\n"
            "Result=exit-code\n"
            "ExecMainStatus=1\n"
            "__INFRALINK_JOURNAL__\n"
            f"{journal}\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )

    response = CliRunner().invoke(
        cli,
        ["--registry", str(registry), "operation", "status", f"ssh/{HOST_ID}/{INVOCATION}"],
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 1
    assert payload["result"]["failure"]["journal"] == [
        "code: first_failure",
        "stage: inspect",
        "retryable: false",
        "code: second_failure",
        "stage: apply",
        "retryable: true",
    ]


def test_operation_status_uses_current_invocation_before_old_success_journal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            f"InvocationID={INVOCATION}\n"
            "ActiveState=activating\n"
            "Result=success\n"
            "ExecMainStatus=0\n"
            '{"ok":true,"run_id":"prior-success-is-not-terminal"}\n'
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    operation_id = f"ssh/{HOST_ID}/{INVOCATION}"

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "operation", "status", operation_id]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert payload["result"]["operation"] == {"id": operation_id, "state": "applying"}
    assert "failure" not in payload["result"]


def test_operation_status_does_not_read_journal_properties_as_current_unit_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    registry = _registry_checkout(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.operations.subprocess.run",
        lambda *args, **kwargs: _completed(
            f"InvocationID={INVOCATION}\n"
            "ActiveState=activating\n"
            "Result=success\n"
            "ExecMainStatus=0\n"
            "__INFRALINK_JOURNAL__\n"
            "ActiveState=inactive\n"
            "Result=success\n"
            "ExecMainStatus=0\n"
        ),
    )
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/known-hosts")),
    )
    operation_id = f"ssh/{HOST_ID}/{INVOCATION}"

    response = CliRunner().invoke(
        cli, ["--registry", str(registry), "operation", "status", operation_id]
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code == 0
    assert payload["result"]["operation"] == {"id": operation_id, "state": "applying"}


def test_operation_status_refuses_a_run_reference_for_an_undeclared_host(tmp_path: Path) -> None:
    registry = _registry_checkout(tmp_path, declared=False)

    response = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(registry),
            "operation",
            "status",
            f"ssh/{HOST_ID}/{INVOCATION}",
        ],
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"


def test_operation_status_explicitly_rejects_a_legacy_control_plane_reference() -> None:
    response = CliRunner().invoke(cli, ["operation", "status", "op_01J00000000000000000000000"])

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "provider_unavailable"
    assert "legacy" in payload["error"]["message"].lower()
