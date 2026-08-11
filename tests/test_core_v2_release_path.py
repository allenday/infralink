"""Hermetic integration coverage for the selected core-v2 release path."""

from __future__ import annotations

import json
import shutil
import subprocess
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from infralink.cli.errors import CliFailure
from infralink.cli.main import cli
from infralink.cli.operations import resolve_apply_request
from infralink.core.registry import Registry
from infralink.host_readiness import HostReadinessProbe
from tests.cli_helpers import assert_schema

FIXTURES = Path(__file__).with_name("fixtures") / "core_v2_release_path"
HOST_ID = "9157ddeb-cb6d-4d55-8252-9db358f5d932"
HOST_NAME = "cyberstorm-citadel"
SENTINEL_SECRET = "PRIVATE-KEY-MUST-NOT-LEAK"


@dataclass(frozen=True)
class CoreReleasePath:
    """The explicitly selected registry declaration used by every CLI call."""

    registry_commit: str
    registry_path: Path
    host_id: str


def _document(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _select_release(registry_path: Path, *, pointer: dict[str, Any], admission: dict[str, Any], candidate: dict[str, Any]) -> CoreReleasePath:
    """Fail closed unless all signed public artifacts select the same declaration."""
    expected = candidate["registry_commit"]
    if pointer.get("channel") != "core-v2" or candidate.get("channel") != "core-v2":
        raise ValueError("core-v2 channel mismatch")
    if pointer.get("registry_commit") != expected or admission.get("registry_commit") != expected:
        raise ValueError("release artifacts select different registry commits")
    if candidate.get("main_registry_commit") == expected:
        raise ValueError("selected candidate must not be main")
    if admission.get("signer_host_uuid") != candidate.get("consumer_host_uuid"):
        raise ValueError("signer host does not match selected candidate consumer")
    if candidate.get("registry_tree") != "candidate-registry/hosts":
        raise ValueError("candidate does not select the rendered registry tree")
    selection = json.loads((registry_path.parent / "release-selection.json").read_text(encoding="utf-8"))
    if selection.get("registry_commit") != expected:
        raise ValueError("selected registry checkout does not match candidate")
    if not (registry_path / candidate["consumer_host_uuid"] / "manifest.yml").is_file():
        raise ValueError("selected candidate host declaration is missing")
    return CoreReleasePath(expected, registry_path, candidate["consumer_host_uuid"])


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True)


def _selected_release(tmp_path: Path) -> CoreReleasePath:
    root = tmp_path / "selected-registry"
    shutil.copytree(FIXTURES / "candidate-registry", root)
    registry = root / "hosts"
    _git(root, "init", "--quiet")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "user.name", "Test")
    _git(root, "add", ".")
    _git(root, "commit", "--quiet", "-m", "selected core-v2 candidate")
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert not status, status
    return _select_release(
        registry,
        pointer=_document("pointer.json"),
        admission=_document("admission.json"),
        candidate=_document("candidate.json"),
    )


def _completed(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=stdout, stderr="")


def _healthy_probe() -> HostReadinessProbe:
    return HostReadinessProbe(
        reachable=True,
        hostname=HOST_NAME,
        machine_id="fixture-machine-id",
        commands={"git": True, "docker": True, "tailscale": True, "jq": True, "bws": True},
        devops_account=True,
        devops_authorized_access=True,
        bws_config=True,
        self_deploy_dependencies=True,
        self_deploy_runtime=True,
        self_deploy_timer_enabled=True,
        self_deploy_timer_active=True,
        self_deploy_mode="v2_reconcile",
        registry_layout="v2_managed",
        requires_v2_registry_layout=True,
        self_deploy_reconcile_result="success",
        self_deploy_reconcile_exit_status=0,
        self_deploy_reconcile_active_state="inactive",
        self_deploy_reconcile_sub_state="dead",
        self_deploy_reconcile_exit_timestamp_monotonic=1,
        error=None,
    )


def test_selected_core_v2_declaration_drives_verifier_dry_apply_and_doctor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    release = _selected_release(tmp_path)
    calls: list[list[str]] = []
    real_run = subprocess.run
    verifier_output = "\n".join(
        (
            "registry_remote=ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git",
            "registry_ref=refs/heads/main",
            "runtime_revision=" + "b" * 40,
            "allowed_signer_principal=infra",
            "allowed_signer_fingerprint=SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            "allowed_signers_sha256=" + "c" * 64,
            "git_ssh_signature_capable=true",
            "fetched_tip=" + "d" * 40,
            "signature_verification=passed",
            f"private_key={SENTINEL_SECRET}",
        )
    )

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        if args[0] == "git":
            return real_run(args, **_)
        calls.append(args)
        return _completed(verifier_output)

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/core-v2-known-hosts")),
    )
    monkeypatch.setattr(
        "infralink.cli.doctor.SshReadinessTransport.probe",
        lambda self, address: _healthy_probe(),
    )
    monkeypatch.setattr(
        "infralink.cli.doctor._fetch_gatus_statuses",
        lambda url, token: [
            {
                "name": "citadel-self-deploy",
                "results": [{"success": True, "timestamp": "2026-08-11T00:00:00Z"}],
            }
        ],
    )

    verifier = CliRunner().invoke(
        cli, ["--registry", str(release.registry_path), "host", "verifier", release.host_id]
    )
    dry_apply = CliRunner().invoke(
        cli,
        ["--registry", str(release.registry_path), "host", "apply", release.host_id, "--dry-run"],
    )
    doctor = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(release.registry_path),
            "doctor",
            "--observation-plan",
            str(FIXTURES / "observation-plan.json"),
            "--adapter-bindings",
            str(FIXTURES / "adapter-bindings.yml"),
            "--gatus-url",
            "http://gatus.test",
            "host",
            release.host_id,
        ],
    )

    verifier_payload = yaml.safe_load(verifier.output)
    apply_payload = yaml.safe_load(dry_apply.output)
    doctor_payload = yaml.safe_load(doctor.output)
    assert verifier.exit_code == dry_apply.exit_code == doctor.exit_code == 0
    assert_schema(verifier_payload, "host-verifier")
    assert_schema(apply_payload, "host-apply")
    assert_schema(doctor_payload, "doctor")
    assert verifier_payload["result"]["target"]["id"] == release.host_id
    assert apply_payload["result"]["target"]["id"] == release.host_id
    assert doctor_payload["result"]["target"]["id"] == release.host_id
    assert doctor_payload["result"]["status"] == "healthy"
    assert calls[0][calls[0].index("--") + 1] == "verifier"
    assert "release-admission-shadow-source" not in verifier.output
    assert SENTINEL_SECRET not in "\n".join((verifier.output, dry_apply.output, doctor.output))


@pytest.mark.parametrize(
    ("artifact", "field", "value", "message"),
    (
        ("pointer.json", "registry_commit", "a" * 40, "release artifacts select different registry commits"),
        ("admission.json", "registry_commit", "a" * 40, "release artifacts select different registry commits"),
        ("admission.json", "signer_host_uuid", "0" * 8 + "-0000-4000-8000-000000000000", "signer host does not match selected candidate consumer"),
    ),
)
def test_release_path_rejects_pointer_mixing_or_signer_host_mismatch(
    tmp_path: Path, artifact: str, field: str, value: str, message: str
) -> None:
    root = tmp_path / "selected-registry"
    shutil.copytree(FIXTURES / "candidate-registry", root)
    registry = root / "hosts"
    pointer = _document("pointer.json")
    admission = _document("admission.json")
    candidate = _document("candidate.json")
    {"pointer.json": pointer, "admission.json": admission}[artifact][field] = value

    with pytest.raises(ValueError, match=message):
        _select_release(registry, pointer=pointer, admission=admission, candidate=candidate)


def test_release_path_rejects_main_checkout_when_pointer_selects_a_candidate() -> None:
    with pytest.raises(ValueError, match="selected registry checkout does not match candidate"):
        _select_release(
            FIXTURES / "registry-main" / "hosts",
            pointer=_document("pointer.json"),
            admission=_document("admission.json"),
            candidate=_document("candidate.json"),
        )


def test_release_path_rejects_legacy_contract_when_a_partial_v2_manifest_is_selected(
    tmp_path: Path,
) -> None:
    release = _selected_release(tmp_path)
    manifest = release.registry_path / release.host_id / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "    self_deploy_v2_reconcile_enabled: true\n",
        encoding="utf-8",
    )
    target = Registry.load_dir(release.registry_path).get(release.host_id)

    assert target is not None
    with pytest.raises(CliFailure, match="Host apply manifest does not package V2 reconcile"):
        resolve_apply_request(release.registry_path, target)
