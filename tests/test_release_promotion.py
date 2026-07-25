import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "verify_release_promotion.py"
SOURCE_SHA = "a" * 40
RUN_ID = "12345"
ARTIFACT_ID = "67890"
WHEEL = "infralink-0.2.0-py3-none-any.whl"
SDIST = "infralink-0.2.0.tar.gz"
PRIVATE_GATE = {
    "infra_management_commit": "d" * 40,
    "contract_harness_sha256": "b" * 64,
    "dependency_lock_sha256": "c" * 64,
}


def load_module():
    spec = importlib.util.spec_from_file_location("verify_release_promotion", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_candidate(root: Path) -> dict[str, str]:
    root.mkdir()
    (root / WHEEL).write_bytes(b"wheel")
    (root / SDIST).write_bytes(b"sdist")
    artifacts = {
        WHEEL: sha256(root / WHEEL),
        SDIST: sha256(root / SDIST),
    }
    manifest = {
        "version": "0.2.0",
        "source_commit": SOURCE_SHA,
        "workflow_run_id": RUN_ID,
        "artifacts": [
            {"name": WHEEL, "sha256": artifacts[WHEEL]},
            {"name": SDIST, "sha256": artifacts[SDIST]},
        ],
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (root / "SHA256SUMS").write_text(
        f"{artifacts[WHEEL]}  {WHEEL}\n{artifacts[SDIST]}  {SDIST}\n",
        encoding="utf-8",
    )
    return {name: sha256(root / name) for name in (*artifacts, "manifest.json", "SHA256SUMS")}


def write_evidence(path: Path, digests: dict[str, str]) -> None:
    evidence = {
        "artifact_digests": digests,
        "candidate_wheel_sha256": digests[WHEEL],
        "contract_harness_sha256": "b" * 64,
        "dependency_lock_sha256": "c" * 64,
        "github_artifact_id": ARTIFACT_ID,
        "github_workflow_run_id": RUN_ID,
        "infra_management_commit": "d" * 40,
        "registry_commit": "e" * 40,
        "source_sha": SOURCE_SHA,
        "status": "pass",
        "version": "0.2.0",
        "woodpecker_pipeline": "42",
        "woodpecker_repo": "relaxgg/infra-management",
    }
    path.write_text(
        json.dumps(evidence, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_attestations(root: Path, candidate: Path) -> None:
    root.mkdir()
    for name in (WHEEL, SDIST, "manifest.json", "SHA256SUMS"):
        statement = {
            "predicateType": "https://slsa.dev/provenance/v1",
            "subject": [{"name": name, "digest": {"sha256": sha256(candidate / name)}}],
            "predicate": {
                "buildDefinition": {
                    "externalParameters": {
                        "workflow": {
                            "path": ".github/workflows/release-candidate.yml",
                            "ref": "refs/heads/main",
                            "repository": "https://github.com/cyberstorm-dev/infralink",
                        }
                    },
                    "resolvedDependencies": [
                        {
                            "digest": {"gitCommit": SOURCE_SHA},
                            "uri": (
                                "git+https://github.com/cyberstorm-dev/infralink@refs/heads/main"
                            ),
                        }
                    ],
                },
                "runDetails": {
                    "builder": {
                        "id": (
                            "https://github.com/cyberstorm-dev/infralink/"
                            ".github/workflows/release-candidate.yml@refs/heads/main"
                        )
                    },
                    "metadata": {
                        "invocationId": (
                            "https://github.com/cyberstorm-dev/infralink/actions/runs/"
                            f"{RUN_ID}/attempts/1"
                        )
                    },
                },
            },
        }
        (root / f"{name}.json").write_text(
            json.dumps([{"verificationResult": {"statement": statement}}]),
            encoding="utf-8",
        )


def test_verify_promotion_requires_exact_candidate_and_signed_evidence(tmp_path: Path) -> None:
    module = load_module()
    candidate = tmp_path / "candidate"
    digests = write_candidate(candidate)
    evidence = tmp_path / "woodpecker-evidence.json"
    write_evidence(evidence, digests)
    attestations = tmp_path / "attestations"
    write_attestations(attestations, candidate)
    artifact_metadata = {
        "id": int(ARTIFACT_ID),
        "name": f"infralink-v0.2.0-{SOURCE_SHA}",
        "workflow_run": {"id": int(RUN_ID), "head_sha": SOURCE_SHA},
        "expired": False,
    }
    run_metadata = {
        "id": int(RUN_ID),
        "head_sha": SOURCE_SHA,
        "path": ".github/workflows/release-candidate.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
        "head_repository": {"full_name": "cyberstorm-dev/infralink"},
    }

    result = module.verify_promotion(
        candidate_dir=candidate,
        evidence_path=evidence,
        artifact_metadata=artifact_metadata,
        run_metadata=run_metadata,
        approved_private_gate=PRIVATE_GATE,
        attestation_dir=attestations,
        source_sha=SOURCE_SHA,
        artifact_id=ARTIFACT_ID,
        candidate_run_id=RUN_ID,
    )

    assert result == {
        "artifact_digests": digests,
        "artifact_id": ARTIFACT_ID,
        "candidate_run_id": RUN_ID,
        "source_sha": SOURCE_SHA,
        "version": "0.2.0",
    }


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        (lambda evidence, _candidate: evidence.update(status="fail"), "evidence"),
        (lambda evidence, _candidate: evidence.update(source_sha="f" * 40), "evidence"),
        (
            lambda evidence, _candidate: evidence["artifact_digests"].update({WHEEL: "f" * 64}),
            "evidence",
        ),
        (
            lambda _evidence, candidate: (candidate / WHEEL).write_bytes(b"changed"),
            "attestation|candidate",
        ),
    ],
)
def test_verify_promotion_rejects_mismatches(tmp_path: Path, mutation, match: str) -> None:
    module = load_module()
    candidate = tmp_path / "candidate"
    digests = write_candidate(candidate)
    evidence_path = tmp_path / "woodpecker-evidence.json"
    write_evidence(evidence_path, digests)
    attestations = tmp_path / "attestations"
    write_attestations(attestations, candidate)
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    mutation(evidence, candidate)
    evidence_path.write_text(
        json.dumps(evidence, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(module.PromotionVerificationError, match=match):
        module.verify_promotion(
            candidate_dir=candidate,
            evidence_path=evidence_path,
            artifact_metadata={
                "id": int(ARTIFACT_ID),
                "name": f"infralink-v0.2.0-{SOURCE_SHA}",
                "workflow_run": {"id": int(RUN_ID), "head_sha": SOURCE_SHA},
                "expired": False,
            },
            run_metadata={
                "id": int(RUN_ID),
                "head_sha": SOURCE_SHA,
                "path": ".github/workflows/release-candidate.yml",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "head_branch": "main",
                "head_repository": {"full_name": "cyberstorm-dev/infralink"},
            },
            approved_private_gate=PRIVATE_GATE,
            attestation_dir=attestations,
            source_sha=SOURCE_SHA,
            artifact_id=ARTIFACT_ID,
            candidate_run_id=RUN_ID,
        )


def test_verify_promotion_rejects_unapproved_private_gate(tmp_path: Path) -> None:
    module = load_module()
    candidate = tmp_path / "candidate"
    digests = write_candidate(candidate)
    evidence_path = tmp_path / "woodpecker-evidence.json"
    write_evidence(evidence_path, digests)
    attestations = tmp_path / "attestations"
    write_attestations(attestations, candidate)

    with pytest.raises(module.PromotionVerificationError, match="private gate"):
        module.verify_promotion(
            candidate_dir=candidate,
            evidence_path=evidence_path,
            artifact_metadata={
                "id": int(ARTIFACT_ID),
                "name": f"infralink-v0.2.0-{SOURCE_SHA}",
                "workflow_run": {"id": int(RUN_ID), "head_sha": SOURCE_SHA},
                "expired": False,
            },
            run_metadata={
                "id": int(RUN_ID),
                "head_sha": SOURCE_SHA,
                "path": ".github/workflows/release-candidate.yml",
                "event": "workflow_dispatch",
                "status": "completed",
                "conclusion": "success",
                "head_branch": "main",
                "head_repository": {"full_name": "cyberstorm-dev/infralink"},
            },
            approved_private_gate={
                **PRIVATE_GATE,
                "infra_management_commit": "f" * 40,
            },
            attestation_dir=attestations,
            source_sha=SOURCE_SHA,
            artifact_id=ARTIFACT_ID,
            candidate_run_id=RUN_ID,
        )


def test_verify_promotion_rejects_attestation_from_another_run(tmp_path: Path) -> None:
    module = load_module()
    candidate = tmp_path / "candidate"
    digests = write_candidate(candidate)
    evidence_path = tmp_path / "woodpecker-evidence.json"
    write_evidence(evidence_path, digests)
    attestations = tmp_path / "attestations"
    write_attestations(attestations, candidate)
    wheel_attestation = attestations / f"{WHEEL}.json"
    wheel_attestation.write_text(
        wheel_attestation.read_text().replace(f"/{RUN_ID}/attempts/", "/999/attempts/"),
        encoding="utf-8",
    )

    with pytest.raises(module.PromotionVerificationError, match="attestation"):
        module.validate_attestations(
            attestations,
            candidate_dir=candidate,
            source_sha=SOURCE_SHA,
            candidate_run_id=RUN_ID,
        )


def test_verify_promotion_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    module = load_module()
    candidate = tmp_path / "candidate"
    digests = write_candidate(candidate)
    evidence = tmp_path / "woodpecker-evidence.json"
    write_evidence(evidence, digests)
    evidence.write_text(
        evidence.read_text(encoding="utf-8").replace(
            '{"artifact_digests":', '{"status":"pass","artifact_digests":'
        ),
        encoding="utf-8",
    )

    with pytest.raises(module.PromotionVerificationError, match="evidence"):
        module.load_json_file(evidence, label="evidence")


def test_validate_evidence_oci_manifest_binds_exact_two_layers(tmp_path: Path) -> None:
    module = load_module()
    evidence = tmp_path / "woodpecker-evidence.json"
    bundle = tmp_path / "woodpecker-evidence.sigstore.json"
    evidence.write_bytes(b'{"status":"pass"}\n')
    bundle.write_bytes(b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}\n')
    repository = "ghcr.io/cyberstorm-dev/infralink-gate-evidence"
    digest = "sha256:" + "f" * 64

    layers = [
        module.oci_descriptor(evidence, module.EVIDENCE_MEDIA_TYPE),
        module.oci_descriptor(bundle, module.BUNDLE_MEDIA_TYPE),
    ]
    manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "reference": f"{repository}@{digest}",
        "digest": digest,
        "content": {
            "schemaVersion": 2,
            "mediaType": "application/vnd.oci.image.manifest.v1+json",
            "layers": layers,
        },
    }

    module.validate_evidence_oci_manifest(
        manifest,
        repository=repository,
        digest=digest,
        evidence=evidence,
        bundle=bundle,
    )
    manifest["content"]["layers"].reverse()
    with pytest.raises(module.PromotionVerificationError, match="OCI"):
        module.validate_evidence_oci_manifest(
            manifest,
            repository=repository,
            digest=digest,
            evidence=evidence,
            bundle=bundle,
        )


@pytest.mark.parametrize(
    ("local_target", "remote_target", "expected"),
    [
        (None, None, "create"),
        (SOURCE_SHA, None, "push"),
        (SOURCE_SHA, SOURCE_SHA, "reuse"),
        (None, SOURCE_SHA, "fetch"),
    ],
)
def test_tag_plan_allows_only_same_target_recovery(
    local_target: str | None,
    remote_target: str | None,
    expected: str,
) -> None:
    module = load_module()
    assert (
        module.tag_plan(
            source_sha=SOURCE_SHA,
            local_target=local_target,
            remote_target=remote_target,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("local_target", "remote_target"),
    [(SOURCE_SHA, "f" * 40), ("f" * 40, None), ("f" * 40, SOURCE_SHA)],
)
def test_tag_plan_rejects_different_target(
    local_target: str | None, remote_target: str | None
) -> None:
    module = load_module()
    with pytest.raises(module.PromotionVerificationError, match="tag"):
        module.tag_plan(
            source_sha=SOURCE_SHA,
            local_target=local_target,
            remote_target=remote_target,
        )
