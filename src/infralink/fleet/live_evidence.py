"""Bounded, local-only reader for controller-produced fleet evidence."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import yaml
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from infralink.cli.contracts import FleetLiveEvidence, FleetValidationDiagnostic
from infralink.fleet.prometheus_evidence import FleetPrometheusEvidence
from infralink.operator_config import (
    FleetPrometheusEvidenceConfig,
    OperatorConfigError,
    configured_fleet_prometheus_evidence,
)
from infralink.operator_sources import LoadedSources

_DECLARATION = "operations/observation/fleet-prometheus-targets.yml"
_KEY_ID = r"^[a-z][a-z0-9-]{0,127}$"
_BINDING_REF = r"^[a-z][a-z0-9-]{0,127}/[a-z][a-z0-9-]{0,127}$"
_REVISION = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_MAX_ARTIFACT_BYTES = 1_048_576
_MAX_GIT_METADATA_BYTES = 1_048_576


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FleetPrometheusTarget(_StrictModel):
    id: str = Field(pattern=_KEY_ID)


class FleetPrometheusControllerBindings(_StrictModel):
    prometheus_credential_binding_ref: str = Field(pattern=_BINDING_REF)
    signing_binding_ref: str = Field(pattern=_BINDING_REF)


class FleetPrometheusTargets(_StrictModel):
    """Registry-owned identities expected in one evidence artifact."""

    schema_version: Literal["infralink.fleet-prometheus-targets/v1"]
    controller_bindings: FleetPrometheusControllerBindings
    targets: list[FleetPrometheusTarget] = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def require_unique_target_ids(self) -> FleetPrometheusTargets:
        ids = [target.id for target in self.targets]
        if len(ids) != len(set(ids)):
            raise ValueError("targets must have unique ids")
        return self


@dataclass(frozen=True)
class LiveEvidenceEvaluation:
    diagnostics: tuple[FleetValidationDiagnostic, ...]
    freshness: FleetLiveEvidence


def evaluate_live_evidence(
    sources: LoadedSources, *, now: datetime | None = None
) -> LiveEvidenceEvaluation:
    """Read and verify one configured artifact without network or process access."""

    now = now or datetime.now(timezone.utc)
    try:
        config = configured_fleet_prometheus_evidence()
    except OperatorConfigError:
        return _failure(
            "live_evidence_unavailable", "Live evidence configuration could not be loaded"
        )
    if config is None:
        return _failure(
            "live_evidence_unavailable", "Live evidence is not configured for this operator"
        )

    declaration = _load_declaration(sources.registry_path)
    if declaration is None:
        return _failure(
            "live_evidence_targets_invalid", "Registry live-evidence targets could not be loaded"
        )
    revision = _registry_revision(sources.registry_path)
    if revision is None:
        return _failure(
            "live_evidence_revision_unavailable", "Selected registry revision could not be resolved"
        )
    artifact = _load_artifact(config)
    if artifact is None:
        return _failure(
            "live_evidence_unavailable", "Configured live evidence artifact is unavailable"
        )
    evidence = _parse_evidence(artifact)
    if evidence is None:
        return _failure("live_evidence_invalid", "Configured live evidence artifact is invalid")
    freshness = FleetLiveEvidence(
        status="fresh" if evidence.is_fresh_at(now) else "stale",
        generated_at=evidence.generated_at,
        max_age_seconds=evidence.max_age_seconds,
    )
    if evidence.registry_revision != revision:
        return _failure(
            "live_evidence_revision_mismatch",
            "Live evidence does not match the selected registry revision",
            freshness=freshness,
        )
    if not config.authorizes_signing_key(
        declaration.controller_bindings.signing_binding_ref, evidence.signature.key_id
    ):
        return _failure(
            "live_evidence_key_unauthorized",
            "Live evidence key is not authorized for the Registry signing binding",
            freshness=freshness,
        )
    public_key = _trusted_public_key(config, evidence.signature.key_id)
    if public_key is None or not evidence.verify_signature(public_key):
        return _failure(
            "live_evidence_signature_invalid",
            "Live evidence signature is not trusted for the selected operator",
            freshness=freshness,
        )
    if freshness.status == "stale":
        return _failure(
            "live_evidence_stale",
            "Live evidence has exceeded its signed freshness window",
            freshness=freshness,
        )

    expected = {target.id for target in declaration.targets}
    actual = set(evidence.targets)
    if expected != actual:
        return _failure(
            "live_evidence_coverage_incomplete",
            "Live evidence does not cover exactly the Registry-declared targets",
            freshness=freshness,
        )
    diagnostics: list[FleetValidationDiagnostic] = []
    for target_id in sorted(expected):
        target = evidence.targets[target_id]
        if target.status == "query_error":
            diagnostics.append(
                _diagnostic(
                    "live_evidence_provider_failure",
                    "Prometheus evidence producer reported a provider failure",
                    target_id,
                )
            )
        elif target.status == "absent":
            diagnostics.append(
                _diagnostic(
                    "live_evidence_target_absent",
                    "Prometheus evidence producer found no recent sample",
                    target_id,
                )
            )
    return LiveEvidenceEvaluation(tuple(diagnostics), freshness)


def _load_declaration(root: Path) -> FleetPrometheusTargets | None:
    try:
        value = yaml.safe_load((root / _DECLARATION).read_text(encoding="utf-8"))
        return FleetPrometheusTargets.model_validate(value)
    except (OSError, ValidationError, yaml.YAMLError):
        return None


def _load_artifact(config: FleetPrometheusEvidenceConfig) -> dict[str, object] | None:
    try:
        with Path(config.artifact_path).open("rb") as artifact:
            raw = artifact.read(_MAX_ARTIFACT_BYTES + 1)
        if len(raw) > _MAX_ARTIFACT_BYTES:
            return None
        value = json.loads(raw)
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _parse_evidence(value: dict[str, object]) -> FleetPrometheusEvidence | None:
    try:
        return FleetPrometheusEvidence.model_validate(value)
    except ValidationError:
        return None


def _trusted_public_key(
    config: FleetPrometheusEvidenceConfig, key_id: str
) -> Ed25519PublicKey | None:
    encoded = config.trusted_public_keys.get(key_id)
    if encoded is None:
        return None
    try:
        raw = base64.b64decode(encoded, validate=True)
        if len(raw) != 32:
            return None
        return Ed25519PublicKey.from_public_bytes(raw)
    except ValueError:
        return None


def _registry_revision(root: Path) -> str | None:
    git = _git_directory(root)
    if git is None:
        return None
    common = _common_git_directory(git)
    head = _read_git_metadata(git / "HEAD")
    if common is None or head is None:
        return None
    if head.startswith("ref: "):
        reference = head.removeprefix("ref: ")
        if not reference.startswith("refs/") or ".." in Path(reference).parts:
            return None
        value = _read_git_reference(common, reference)
    else:
        value = head
    return value if _REVISION.fullmatch(value) else None


def _git_directory(root: Path) -> Path | None:
    metadata = root / ".git"
    if metadata.is_dir():
        return metadata
    if not metadata.is_file():
        return None
    line = _read_git_metadata(metadata)
    if line is None:
        return None
    if not line.startswith("gitdir: "):
        return None
    configured = Path(line.removeprefix("gitdir: "))
    candidate = configured if configured.is_absolute() else metadata.parent / configured
    return candidate if candidate.is_dir() else None


def _common_git_directory(git: Path) -> Path | None:
    """Resolve only Git's standard linked-worktree common metadata location."""

    declared = git / "commondir"
    if not declared.is_file():
        return git
    value = _read_git_metadata(declared)
    if value is None or not value:
        return None
    configured = Path(value)
    candidate = (configured if configured.is_absolute() else git / configured).resolve()
    expected = git.parent.parent.resolve()
    if git.parent.name != "worktrees" or candidate != expected or not candidate.is_dir():
        return None
    return candidate


def _read_git_reference(git: Path, reference: str) -> str:
    direct = _read_git_metadata(git / reference) or ""
    if direct:
        return direct
    packed_refs = _read_git_metadata(git / "packed-refs")
    if packed_refs is None:
        return ""
    for line in packed_refs.splitlines():
        if not line or line.startswith(("#", "^")):
            continue
        revision, separator, packed_reference = line.partition(" ")
        if separator and packed_reference == reference:
            return revision
    return ""


def _read_git_metadata(path: Path) -> str | None:
    try:
        with path.open("rb") as metadata:
            raw = metadata.read(_MAX_GIT_METADATA_BYTES + 1)
        if len(raw) > _MAX_GIT_METADATA_BYTES:
            return None
        return raw.decode("ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None


def _failure(
    code: str, message: str, *, freshness: FleetLiveEvidence | None = None
) -> LiveEvidenceEvaluation:
    return LiveEvidenceEvaluation(
        (_diagnostic(code, message, "fleet"),),
        freshness
        or FleetLiveEvidence(status="unavailable", generated_at=None, max_age_seconds=None),
    )


def _diagnostic(code: str, message: str, subject_id: str) -> FleetValidationDiagnostic:
    return FleetValidationDiagnostic(
        code=code,
        severity="error",
        message=message,
        subject_kind="fleet" if subject_id == "fleet" else "edge",
        subject_id=subject_id,
        path=None,
    )
