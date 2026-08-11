"""Hermetic integration coverage for the selected core-v2 release path."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
import yaml
from click.testing import CliRunner

from infralink.cli.main import cli
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
    runtime_revision: str
    registry_path: Path
    host_id: str


def _document(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _release_artifacts(
    root: Path = FIXTURES,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    pointer = json.loads((root / "pointer.json").read_text(encoding="utf-8"))
    candidate_name = pointer.get("candidate")
    if candidate_name != "candidate.json":
        raise ValueError("pointer candidate is not the selected candidate artifact")
    candidate = json.loads((root / candidate_name).read_text(encoding="utf-8"))
    admission = json.loads((root / "admission.json").read_text(encoding="utf-8"))
    return pointer, admission, candidate


def _select_release(
    registry_path: Path,
    *,
    pointer: dict[str, Any],
    admission: dict[str, Any],
    candidate: dict[str, Any],
) -> CoreReleasePath:
    """Fail closed unless all signed public artifacts select the same declaration."""
    for artifact, name in ((pointer, "pointer"), (admission, "admission")):
        signature = artifact.get("signature")
        if not isinstance(signature, str) or not signature.startswith("ssh-ed25519 "):
            raise ValueError(f"{name} signature is missing or invalid")
    expected = candidate["registry_commit"]
    runtime_revision = candidate.get("runtime_revision")
    if not isinstance(runtime_revision, str) or len(runtime_revision) != 40:
        raise ValueError("candidate runtime revision is invalid")
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
    selection = json.loads(
        (registry_path.parent / "release-selection.json").read_text(encoding="utf-8")
    )
    if selection.get("registry_commit") != expected:
        raise ValueError("selected registry checkout does not match candidate")
    artifacts = candidate.get("artifacts")
    if not isinstance(artifacts, list) or not all(
        isinstance(path, str) and (registry_path.parent / path).is_file() for path in artifacts
    ):
        raise ValueError("selected candidate artifacts are missing")
    if not (registry_path / candidate["consumer_host_uuid"] / "manifest.yml").is_file():
        raise ValueError("selected candidate host declaration is missing")
    return CoreReleasePath(
        expected, runtime_revision, registry_path, candidate["consumer_host_uuid"]
    )


def _git(root: Path, *args: str) -> None:
    environment = os.environ.copy()
    if args[0] == "commit":
        environment.update(
            {
                "GIT_AUTHOR_DATE": "2026-08-11T00:00:00+0000",
                "GIT_COMMITTER_DATE": "2026-08-11T00:00:00+0000",
            }
        )
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )


def _selected_release(tmp_path: Path) -> CoreReleasePath:
    root = tmp_path / "selected-registry"
    registry = root / "hosts"
    shutil.copytree(FIXTURES / "candidate-registry" / "hosts", registry)
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
    shutil.copyfile(
        FIXTURES / "candidate-registry" / "release-selection.json",
        root / "release-selection.json",
    )
    pointer, admission, candidate = _release_artifacts()
    head = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert head == candidate["registry_commit"]
    return _select_release(
        registry,
        pointer=pointer,
        admission=admission,
        candidate=candidate,
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
    scripts: list[str] = []
    real_run = subprocess.run
    verifier_output = "\n".join(
        (
            "registry_remote=ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git",
            "registry_ref=refs/heads/main",
            "runtime_revision=" + release.runtime_revision,
            "allowed_signer_principal=infra",
            "allowed_signer_fingerprint=SHA256:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB",
            "allowed_signers_sha256=" + "c" * 64,
            "git_ssh_signature_capable=true",
            "fetched_tip=" + "d" * 40,
            "signature_verification=passed",
            f"private_key={SENTINEL_SECRET}",
        )
    )

    def fake_run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        if args[0] == "git":
            return real_run(args, **kwargs)  # type: ignore[arg-type, no-any-return]
        calls.append(args)
        scripts.append(str(kwargs.get("input", "")))
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
    assert verifier_payload["result"]["verifier"]["runtime_revision"] == release.runtime_revision
    assert apply_payload["result"]["target"]["id"] == release.host_id
    assert doctor_payload["result"]["target"]["id"] == release.host_id
    assert doctor_payload["result"]["status"] == "healthy"
    remote_args = calls[0][calls[0].index("--") + 1 :]
    assert remote_args == [
        "verifier",
        "/var/lib/legacy/registry",
        "/var/lib/legacy/runtime/" + release.runtime_revision,
        "/etc/legacy/allowed_signers",
        "ssh://git@gitea.example.invalid:2222/relaxgg/infra-registry.git",
        "refs/heads/main",
    ]
    assert all("release-admission" not in arg for arg in remote_args)
    assert "systemctl start" not in scripts[0]
    assert "release-admission-shadow-source" not in scripts[0]
    assert "/var/lib/release-admission" not in scripts[0]
    assert "release-admission-shadow-source" not in verifier.output
    assert SENTINEL_SECRET not in "\n".join((verifier.output, dry_apply.output, doctor.output))


@pytest.mark.parametrize(
    ("artifact", "field", "value", "message"),
    (
        (
            "pointer.json",
            "registry_commit",
            "a" * 40,
            "release artifacts select different registry commits",
        ),
        (
            "admission.json",
            "registry_commit",
            "a" * 40,
            "release artifacts select different registry commits",
        ),
        (
            "admission.json",
            "signer_host_uuid",
            "0" * 8 + "-0000-4000-8000-000000000000",
            "signer host does not match selected candidate consumer",
        ),
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


def test_release_path_rejects_a_pointer_to_any_artifact_other_than_the_candidate(
    tmp_path: Path,
) -> None:
    fixture_root = tmp_path / "fixtures"
    shutil.copytree(FIXTURES, fixture_root)
    pointer_path = fixture_root / "pointer.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    pointer["candidate"] = "registry-main/release-selection.json"
    pointer_path.write_text(json.dumps(pointer), encoding="utf-8")

    with pytest.raises(
        ValueError, match="pointer candidate is not the selected candidate artifact"
    ):
        _release_artifacts(fixture_root)


def test_release_path_rejects_an_unsigned_admission() -> None:
    pointer, admission, candidate = _release_artifacts()
    admission["signature"] = ""

    with pytest.raises(ValueError, match="admission signature is missing or invalid"):
        _select_release(
            FIXTURES / "candidate-registry" / "hosts",
            pointer=pointer,
            admission=admission,
            candidate=candidate,
        )


def test_host_verifier_uses_legacy_or_active_v2_contract_without_mixing_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    legacy = _selected_release(tmp_path / "legacy")
    current_root = tmp_path / "current"
    current_registry = current_root / "hosts"
    shutil.copytree(FIXTURES / "registry-main" / "hosts", current_registry)
    current_manifest = current_registry / HOST_ID / "manifest.yml"
    current_manifest.write_text(
        current_manifest.read_text(encoding="utf-8")
        + "    self_deploy_v2_reconcile_packaged: true\n"
        + "    self_deploy_v2_promotion_policy_enabled: true\n"
        + "    self_deploy_v2_promotion_channel: core-v2\n"
        + "    self_deploy_v2_promotion_host_fingerprint: ssh-rsa SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n",
        + "    self_deploy_v2_target_ssh_host_fingerprint: ssh-rsa SHA256:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n",
        encoding="utf-8",
    )
    calls: list[list[str]] = []
    public_output = "\n".join(
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
        )
    )

    def fake_run(args: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return _completed(public_output)

    monkeypatch.setattr("infralink.cli.operations.subprocess.run", fake_run)
    monkeypatch.setattr(
        "infralink.cli.operations._pinned_known_hosts",
        lambda request: nullcontext(Path("/tmp/core-v2-known-hosts")),
    )
    legacy_result = CliRunner().invoke(
        cli, ["--registry", str(legacy.registry_path), "host", "verifier", HOST_ID]
    )
    current_result = CliRunner().invoke(
        cli, ["--registry", str(current_registry), "host", "verifier", HOST_ID]
    )

    assert legacy_result.exit_code == current_result.exit_code == 0
    assert calls[0][calls[0].index("--") + 1] == "verifier"
    assert calls[1][calls[1].index("--") + 1 :] == [
        "active-verifier",
        "self-deploy-v2-reconcile.service",
        HOST_ID,
    ]


def test_release_path_rejects_legacy_contract_when_a_partial_v2_manifest_is_selected(
    tmp_path: Path,
) -> None:
    release = _selected_release(tmp_path)
    manifest = release.registry_path / release.host_id / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8") + "    self_deploy_v2_reconcile_enabled: true\n",
        encoding="utf-8",
    )
    response = CliRunner().invoke(
        cli,
        ["--registry", str(release.registry_path), "host", "apply", release.host_id, "--dry-run"],
    )

    payload = yaml.safe_load(response.output)
    assert response.exit_code != 0
    assert payload["error"]["code"] == "input_load_failed"
    assert "Host apply manifest does not package V2 reconcile" in payload["error"]["message"]
