"""Typed, offline public API for observation validation and projection."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from infralink.observation.canonical import canonical_digest
from infralink.observation.diagnostics import Diagnostic, DiagnosticSet, SourceLocation
from infralink.observation.loader import ObservationDocument, load_observation_documents
from infralink.observation.planner import Plan, PlanValidationError, resolve_observation_documents


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Bounded diagnostics produced without evaluating providers or live systems."""

    diagnostics: DiagnosticSet
    document_count: int

    @property
    def valid(self) -> bool:
        return self.diagnostics.error_count == 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Byte and semantic identities for one source document."""

    path: str
    document_index: int
    raw_sha256: str
    semantic_sha256: str


@dataclass(frozen=True, slots=True)
class ProjectResult:
    """A semantic plan plus non-semantic raw source provenance."""

    plan: Plan
    sources: tuple[SourceProvenance, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "plan": self.plan.model_dump(mode="json"),
            "sources": [asdict(source) for source in self.sources],
        }


class ProjectValidationError(ValueError):
    """Declared source input could not be projected into a plan."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(f"observation projection has {report.diagnostics.error_count} error(s)")


def validate(
    paths: Sequence[Path], *, limit: int = 50, as_of: datetime | None = None
) -> ValidationReport:
    """Validate explicit paths; ``as_of`` is required when planning waiver-bearing input."""

    loaded = load_observation_documents(paths, diagnostic_limit=limit)
    if not loaded.valid:
        return ValidationReport(loaded.diagnostics, len(loaded.documents))
    if as_of is None:
        as_of = _as_of_without_waivers(loaded.documents)
    try:
        resolve_observation_documents(loaded.documents, as_of=as_of, diagnostic_limit=limit)
    except PlanValidationError as error:
        return ValidationReport(error.report.diagnostics, len(loaded.documents))
    return ValidationReport(loaded.diagnostics, len(loaded.documents))


def project(
    paths: Sequence[Path], *, registry_revision: str | None = None, as_of: datetime
) -> ProjectResult:
    """Project explicit paths deterministically using the caller's waiver evaluation time."""

    loaded = load_observation_documents(paths, diagnostic_limit=50)
    if not loaded.valid:
        raise ProjectValidationError(ValidationReport(loaded.diagnostics, len(loaded.documents)))
    try:
        plan = resolve_observation_documents(loaded.documents, as_of=as_of, diagnostic_limit=50)
    except PlanValidationError as error:
        raise ProjectValidationError(
            ValidationReport(error.report.diagnostics, len(loaded.documents))
        ) from None
    if registry_revision is not None:
        if not registry_revision:
            raise ValueError("registry_revision must be non-empty when supplied")
        if plan.registry_revision not in (None, registry_revision):
            diagnostic = Diagnostic(
                code="registry-revision-conflict",
                severity="error",
                message="The requested registry revision conflicts with the source revision.",
                location=SourceLocation("<input>", "/registry_revision"),
                identity="registry_revision",
                next_actions=("Use the source registry revision or update the source documents.",),
            )
            raise ProjectValidationError(
                ValidationReport(
                    DiagnosticSet.from_diagnostics([diagnostic], limit=50), len(loaded.documents)
                )
            )
        plan = plan.model_copy(update={"registry_revision": registry_revision, "plan_digest": None})
        plan = plan.model_copy(update={"plan_digest": canonical_digest(plan)})
    return ProjectResult(plan=plan, sources=_source_provenance(loaded.documents))


def _as_of_without_waivers(documents: tuple[ObservationDocument, ...]) -> datetime:
    if any(document.data.get("waivers") for document in documents):
        raise ValueError("as_of is required when validating documents containing waivers")
    # No date-sensitive records exist, so this fixed instant is a deterministic policy.
    return datetime.fromisoformat("1970-01-01T00:00:00+00:00")


def _source_provenance(
    documents: tuple[ObservationDocument, ...],
) -> tuple[SourceProvenance, ...]:
    return tuple(
        SourceProvenance(
            path=document.source_path,
            document_index=document.document_index,
            raw_sha256=document.raw_sha256,
            semantic_sha256=document.semantic_sha256,
        )
        for document in sorted(documents, key=lambda item: (item.source_path, item.document_index))
    )


__all__ = [
    "ProjectResult",
    "ProjectValidationError",
    "SourceProvenance",
    "ValidationReport",
    "project",
    "validate",
]
