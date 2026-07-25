#!/usr/bin/env python3
"""Fail-closed validation for promoting an existing Infralink candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

VERSION = "0.2.0"
WHEEL = "infralink-0.2.0-py3-none-any.whl"
SDIST = "infralink-0.2.0.tar.gz"
CANDIDATE_FILES = (WHEEL, SDIST, "manifest.json", "SHA256SUMS")
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
ID_PATTERN = re.compile(r"[1-9][0-9]*")
OCI_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")
EVIDENCE_REPOSITORY = "ghcr.io/cyberstorm-dev/infralink-gate-evidence"
REPOSITORY = "cyberstorm-dev/infralink"
REPOSITORY_URL = f"https://github.com/{REPOSITORY}"
WORKFLOW_PATH = ".github/workflows/release-candidate.yml"
EVIDENCE_MEDIA_TYPE = "application/vnd.cyberstorm.infralink-gate-evidence.v1+json"
BUNDLE_MEDIA_TYPE = "application/vnd.dev.sigstore.bundle.v0.3+json"


class PromotionVerificationError(Exception):
    """Raised when release inputs do not describe one exact verified candidate."""


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionVerificationError("duplicate JSON key")
        result[key] = value
    return result


def load_json_file(path: Path, *, label: str) -> dict[str, Any]:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise PromotionVerificationError(f"invalid {label}")
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicates,
        )
    except (OSError, UnicodeError, ValueError, PromotionVerificationError):
        raise PromotionVerificationError(f"invalid {label}") from None
    if type(value) is not dict:
        raise PromotionVerificationError(f"invalid {label}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        if not path.is_file() or path.is_symlink():
            raise PromotionVerificationError("invalid candidate artifact")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise PromotionVerificationError("invalid candidate artifact") from None
    return digest.hexdigest()


def oci_descriptor(path: Path, media_type: str) -> dict[str, object]:
    try:
        size = path.stat().st_size
    except OSError:
        raise PromotionVerificationError("invalid OCI evidence layer") from None
    if size > 1024 * 1024:
        raise PromotionVerificationError("invalid OCI evidence layer")
    return {
        "mediaType": media_type,
        "digest": "sha256:" + _sha256(path),
        "size": size,
        "annotations": {"org.opencontainers.image.title": path.name},
    }


def validate_evidence_oci_manifest(
    manifest: dict[str, Any],
    *,
    repository: str,
    digest: str,
    evidence: Path,
    bundle: Path,
) -> None:
    if (
        repository != EVIDENCE_REPOSITORY
        or OCI_DIGEST_PATTERN.fullmatch(digest) is None
        or evidence.parent != bundle.parent
    ):
        raise PromotionVerificationError("invalid evidence OCI reference")
    try:
        evidence_files = {path.name for path in evidence.parent.iterdir()}
    except OSError:
        raise PromotionVerificationError("invalid evidence OCI files") from None
    if evidence_files != {evidence.name, bundle.name}:
        raise PromotionVerificationError("invalid evidence OCI files")
    expected_layers = [
        oci_descriptor(evidence, EVIDENCE_MEDIA_TYPE),
        oci_descriptor(bundle, BUNDLE_MEDIA_TYPE),
    ]
    content = manifest.get("content")
    if (
        manifest.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
        or manifest.get("reference") != f"{repository}@{digest}"
        or manifest.get("digest") != digest
        or type(content) is not dict
        or content.get("schemaVersion") != 2
        or content.get("mediaType") != "application/vnd.oci.image.manifest.v1+json"
        or content.get("layers") != expected_layers
    ):
        raise PromotionVerificationError("invalid evidence OCI manifest")


def _require_sha(value: str, *, label: str) -> None:
    if SHA_PATTERN.fullmatch(value) is None:
        raise PromotionVerificationError(f"invalid {label}")


def _require_id(value: str, *, label: str) -> None:
    if ID_PATTERN.fullmatch(value) is None:
        raise PromotionVerificationError(f"invalid {label}")


def _nested(value: Any, *keys: str) -> Any:
    for key in keys:
        if type(value) is not dict or key not in value:
            return None
        value = value[key]
    return value


def validate_attestations(
    attestation_dir: Path,
    *,
    candidate_dir: Path,
    source_sha: str,
    candidate_run_id: str,
) -> None:
    expected_files = {f"{name}.json" for name in CANDIDATE_FILES}
    try:
        if {path.name for path in attestation_dir.iterdir()} != expected_files:
            raise PromotionVerificationError("invalid attestation set")
    except OSError:
        raise PromotionVerificationError("invalid attestation set") from None
    expected_builder = f"{REPOSITORY_URL}/{WORKFLOW_PATH}@refs/heads/main"
    expected_invocation = re.compile(
        rf"{re.escape(REPOSITORY_URL)}/actions/runs/"
        rf"{re.escape(candidate_run_id)}/attempts/[1-9][0-9]*"
    )
    expected_dependency = {
        "digest": {"gitCommit": source_sha},
        "uri": f"git+{REPOSITORY_URL}@refs/heads/main",
    }
    expected_subjects = {
        name: {"sha256": _sha256(candidate_dir / name)} for name in CANDIDATE_FILES
    }
    selected_statement: dict[str, Any] | None = None
    for name in CANDIDATE_FILES:
        path = attestation_dir / f"{name}.json"
        try:
            if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
                raise PromotionVerificationError("invalid attestation")
            results = json.loads(
                path.read_text(encoding="utf-8"),
                object_pairs_hook=_reject_duplicates,
            )
        except (OSError, UnicodeError, ValueError, PromotionVerificationError):
            raise PromotionVerificationError("invalid attestation") from None
        if type(results) is not list:
            raise PromotionVerificationError("invalid attestation")
        matches = 0
        matching_statement: dict[str, Any] | None = None
        for result in results:
            statement = _nested(result, "verificationResult", "statement")
            subjects = statement.get("subject") if type(statement) is dict else None
            actual_subjects: dict[str, Any] = {}
            if type(subjects) is list:
                for subject in subjects:
                    if (
                        type(subject) is not dict
                        or set(subject) != {"name", "digest"}
                        or type(subject.get("name")) is not str
                        or subject["name"] in actual_subjects
                        or type(subject.get("digest")) is not dict
                    ):
                        actual_subjects = {}
                        break
                    actual_subjects[subject["name"]] = subject["digest"]
            invocation = _nested(
                statement,
                "predicate",
                "runDetails",
                "metadata",
                "invocationId",
            )
            valid = (
                type(statement) is dict
                and statement.get("predicateType") == "https://slsa.dev/provenance/v1"
                and actual_subjects == expected_subjects
                and _nested(
                    statement,
                    "predicate",
                    "buildDefinition",
                    "externalParameters",
                    "workflow",
                )
                == {
                    "path": WORKFLOW_PATH,
                    "ref": "refs/heads/main",
                    "repository": REPOSITORY_URL,
                }
                and _nested(
                    statement,
                    "predicate",
                    "buildDefinition",
                    "resolvedDependencies",
                )
                == [expected_dependency]
                and _nested(statement, "predicate", "runDetails", "builder", "id")
                == expected_builder
                and type(invocation) is str
                and expected_invocation.fullmatch(invocation) is not None
            )
            if valid:
                matches += 1
                matching_statement = statement
        if matches != 1:
            raise PromotionVerificationError("invalid attestation")
        if selected_statement is None:
            selected_statement = matching_statement
        elif matching_statement != selected_statement:
            raise PromotionVerificationError("invalid attestation")


def _validate_github_metadata(
    artifact: dict[str, Any],
    run: dict[str, Any],
    *,
    source_sha: str,
    artifact_id: str,
    candidate_run_id: str,
) -> None:
    expected_artifact = {
        "id": int(artifact_id),
        "name": f"infralink-v{VERSION}-{source_sha}",
        "expired": False,
    }
    if (
        type(artifact.get("id")) is not int
        or type(artifact.get("name")) is not str
        or type(artifact.get("expired")) is not bool
        or any(artifact.get(key) != value for key, value in expected_artifact.items())
    ):
        raise PromotionVerificationError("invalid GitHub artifact metadata")
    artifact_run = artifact.get("workflow_run")
    if (
        type(artifact_run) is not dict
        or type(artifact_run.get("id")) is not int
        or type(artifact_run.get("head_sha")) is not str
        or artifact_run.get("id") != int(candidate_run_id)
        or artifact_run.get("head_sha") != source_sha
    ):
        raise PromotionVerificationError("invalid GitHub artifact metadata")
    expected_run = {
        "id": int(candidate_run_id),
        "head_sha": source_sha,
        "path": ".github/workflows/release-candidate.yml",
        "event": "workflow_dispatch",
        "status": "completed",
        "conclusion": "success",
        "head_branch": "main",
    }
    if (
        type(run.get("id")) is not int
        or any(type(run.get(key)) is not str for key in expected_run if key != "id")
        or any(run.get(key) != value for key, value in expected_run.items())
    ):
        raise PromotionVerificationError("invalid GitHub candidate run metadata")
    head_repository = run.get("head_repository")
    if (
        type(head_repository) is not dict
        or head_repository.get("full_name") != "cyberstorm-dev/infralink"
    ):
        raise PromotionVerificationError("invalid GitHub candidate run metadata")


def _validate_manifest(
    candidate_dir: Path,
    *,
    source_sha: str,
    candidate_run_id: str,
) -> dict[str, str]:
    try:
        names = {path.name for path in candidate_dir.iterdir()}
    except OSError:
        raise PromotionVerificationError("invalid candidate directory") from None
    if names != set(CANDIDATE_FILES):
        raise PromotionVerificationError("invalid candidate file set")

    manifest = load_json_file(candidate_dir / "manifest.json", label="candidate manifest")
    if (
        set(manifest) != {"version", "source_commit", "workflow_run_id", "artifacts"}
        or manifest.get("version") != VERSION
        or manifest.get("source_commit") != source_sha
        or manifest.get("workflow_run_id") != candidate_run_id
    ):
        raise PromotionVerificationError("invalid candidate manifest")
    artifacts = manifest.get("artifacts")
    if type(artifacts) is not list or len(artifacts) != 2:
        raise PromotionVerificationError("invalid candidate manifest")

    manifest_digests: dict[str, str] = {}
    for artifact in artifacts:
        if (
            type(artifact) is not dict
            or set(artifact) != {"name", "sha256"}
            or artifact.get("name") not in {WHEEL, SDIST}
            or type(artifact.get("sha256")) is not str
            or DIGEST_PATTERN.fullmatch(artifact["sha256"]) is None
            or artifact["name"] in manifest_digests
        ):
            raise PromotionVerificationError("invalid candidate manifest")
        manifest_digests[artifact["name"]] = artifact["sha256"]
    if set(manifest_digests) != {WHEEL, SDIST}:
        raise PromotionVerificationError("invalid candidate manifest")

    computed = {name: _sha256(candidate_dir / name) for name in CANDIDATE_FILES}
    if any(computed[name] != digest for name, digest in manifest_digests.items()):
        raise PromotionVerificationError("candidate digest mismatch")
    expected_sums = "".join(f"{manifest_digests[name]}  {name}\n" for name in (WHEEL, SDIST))
    try:
        sums = (candidate_dir / "SHA256SUMS").read_text(encoding="ascii")
    except (OSError, UnicodeError):
        raise PromotionVerificationError("invalid candidate checksums") from None
    if sums != expected_sums:
        raise PromotionVerificationError("invalid candidate checksums")
    return computed


def _validate_evidence(
    evidence_path: Path,
    *,
    source_sha: str,
    artifact_id: str,
    candidate_run_id: str,
    candidate_digests: dict[str, str],
    approved_private_gate: dict[str, Any],
) -> None:
    evidence = load_json_file(evidence_path, label="evidence")
    required = {
        "artifact_digests",
        "candidate_wheel_sha256",
        "contract_harness_sha256",
        "dependency_lock_sha256",
        "github_artifact_id",
        "github_workflow_run_id",
        "infra_management_commit",
        "registry_commit",
        "source_sha",
        "status",
        "version",
        "woodpecker_pipeline",
        "woodpecker_repo",
    }
    if (
        set(evidence) != required
        or evidence.get("status") != "pass"
        or evidence.get("version") != VERSION
        or evidence.get("source_sha") != source_sha
        or evidence.get("github_artifact_id") != artifact_id
        or evidence.get("github_workflow_run_id") != candidate_run_id
        or evidence.get("woodpecker_repo") != "relaxgg/infra-management"
        or evidence.get("artifact_digests") != candidate_digests
        or evidence.get("candidate_wheel_sha256") != candidate_digests[WHEEL]
    ):
        raise PromotionVerificationError("invalid evidence")
    for field in ("infra_management_commit", "registry_commit"):
        if type(evidence.get(field)) is not str or SHA_PATTERN.fullmatch(evidence[field]) is None:
            raise PromotionVerificationError("invalid evidence")
    for field in ("woodpecker_pipeline",):
        if type(evidence.get(field)) is not str or ID_PATTERN.fullmatch(evidence[field]) is None:
            raise PromotionVerificationError("invalid evidence")
    for field in ("contract_harness_sha256", "dependency_lock_sha256"):
        if (
            type(evidence.get(field)) is not str
            or DIGEST_PATTERN.fullmatch(evidence[field]) is None
        ):
            raise PromotionVerificationError("invalid evidence")
    private_fields = {
        "infra_management_commit",
        "contract_harness_sha256",
        "dependency_lock_sha256",
    }
    if set(approved_private_gate) != private_fields or any(
        evidence[field] != approved_private_gate.get(field) for field in private_fields
    ):
        raise PromotionVerificationError("unapproved private gate")
    try:
        raw = evidence_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise PromotionVerificationError("invalid evidence") from None
    if raw != json.dumps(evidence, separators=(",", ":"), sort_keys=True) + "\n":
        raise PromotionVerificationError("noncanonical evidence")


def verify_promotion(
    *,
    candidate_dir: Path,
    evidence_path: Path,
    artifact_metadata: dict[str, Any],
    run_metadata: dict[str, Any],
    approved_private_gate: dict[str, Any],
    attestation_dir: Path,
    source_sha: str,
    artifact_id: str,
    candidate_run_id: str,
) -> dict[str, object]:
    _require_sha(source_sha, label="source SHA")
    _require_id(artifact_id, label="artifact ID")
    _require_id(candidate_run_id, label="candidate run ID")
    _validate_github_metadata(
        artifact_metadata,
        run_metadata,
        source_sha=source_sha,
        artifact_id=artifact_id,
        candidate_run_id=candidate_run_id,
    )
    validate_attestations(
        attestation_dir,
        candidate_dir=candidate_dir,
        source_sha=source_sha,
        candidate_run_id=candidate_run_id,
    )
    digests = _validate_manifest(
        candidate_dir,
        source_sha=source_sha,
        candidate_run_id=candidate_run_id,
    )
    _validate_evidence(
        evidence_path,
        source_sha=source_sha,
        artifact_id=artifact_id,
        candidate_run_id=candidate_run_id,
        candidate_digests=digests,
        approved_private_gate=approved_private_gate,
    )
    return {
        "artifact_digests": digests,
        "artifact_id": artifact_id,
        "candidate_run_id": candidate_run_id,
        "source_sha": source_sha,
        "version": VERSION,
    }


def tag_plan(
    *,
    source_sha: str,
    local_target: str | None,
    remote_target: str | None,
) -> str:
    _require_sha(source_sha, label="source SHA")
    for target in (local_target, remote_target):
        if target is not None and target != source_sha:
            raise PromotionVerificationError("release tag targets another commit")
    if local_target is None and remote_target is None:
        return "create"
    if local_target is None:
        return "fetch"
    if remote_target is None:
        return "push"
    return "reuse"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-dir", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    parser.add_argument("--evidence-bundle", required=True, type=Path)
    parser.add_argument("--evidence-oci-manifest", required=True, type=Path)
    parser.add_argument("--evidence-oci-digest", required=True)
    parser.add_argument("--artifact-metadata", required=True, type=Path)
    parser.add_argument("--run-metadata", required=True, type=Path)
    parser.add_argument("--approved-private-gate", required=True, type=Path)
    parser.add_argument("--attestation-dir", required=True, type=Path)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--artifact-id", required=True)
    parser.add_argument("--candidate-run-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        validate_evidence_oci_manifest(
            load_json_file(args.evidence_oci_manifest, label="evidence OCI manifest"),
            repository=EVIDENCE_REPOSITORY,
            digest=args.evidence_oci_digest,
            evidence=args.evidence,
            bundle=args.evidence_bundle,
        )
        result = verify_promotion(
            candidate_dir=args.candidate_dir,
            evidence_path=args.evidence,
            artifact_metadata=load_json_file(
                args.artifact_metadata, label="GitHub artifact metadata"
            ),
            run_metadata=load_json_file(args.run_metadata, label="GitHub run metadata"),
            approved_private_gate=load_json_file(
                args.approved_private_gate, label="approved private gate"
            ),
            attestation_dir=args.attestation_dir,
            source_sha=args.source_sha,
            artifact_id=args.artifact_id,
            candidate_run_id=args.candidate_run_id,
        )
        args.output.write_text(
            json.dumps(result, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except (OSError, PromotionVerificationError):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
