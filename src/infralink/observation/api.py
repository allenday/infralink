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
    paths: Sequence[Path],
    *,
    limit: int = 50,
    as_of: datetime,
    registry_revision: str | None = None,
) -> ValidationReport:
    """Validate explicit paths at a caller-supplied, timezone-aware instant."""

    loaded = load_observation_documents(paths, diagnostic_limit=limit)
    phases = [loaded.diagnostics]
    invalid_as_of = _invalid_as_of_diagnostic(as_of)
    if invalid_as_of is not None:
        phases.append(DiagnosticSet.from_diagnostics([invalid_as_of], limit=limit))
    else:
        if registry_revision is not None and not registry_revision.strip():
            phases.append(
                DiagnosticSet.from_diagnostics(
                    [
                        _argument_diagnostic(
                            "invalid-registry-revision",
                            "/registry_revision",
                            "registry_revision",
                            "Supply a non-empty registry revision string or omit it.",
                        )
                    ],
                    limit=limit,
                )
            )
        try:
            plan = resolve_observation_documents(
                loaded.documents, as_of=as_of, diagnostic_limit=limit
            )
            if (
                registry_revision is not None
                and registry_revision.strip()
                and plan.registry_revision not in (None, registry_revision)
            ):
                phases.append(
                    DiagnosticSet.from_diagnostics(
                        [
                            _argument_diagnostic(
                                "registry-revision-conflict",
                                "/registry_revision",
                                "registry_revision",
                                "Use the source registry revision or update the source documents.",
                            )
                        ],
                        limit=limit,
                    )
                )
        except PlanValidationError as error:
            phases.append(error.report.diagnostics)
    return ValidationReport(
        _combine_diagnostics(phases, limit=limit), loaded.attempted_document_count
    )


def project(
    paths: Sequence[Path], *, registry_revision: str | None = None, as_of: datetime
) -> ProjectResult:
    """Project explicit paths deterministically using the caller's waiver evaluation time."""

    loaded = load_observation_documents(paths, diagnostic_limit=50)
    phases = [loaded.diagnostics]
    argument_findings: list[Diagnostic] = []
    invalid_as_of = _invalid_as_of_diagnostic(as_of)
    if invalid_as_of is not None:
        argument_findings.append(invalid_as_of)
    if registry_revision is not None:
        if not isinstance(registry_revision, str) or not registry_revision.strip():
            argument_findings.append(
                _argument_diagnostic(
                    "invalid-registry-revision",
                    "/registry_revision",
                    "registry_revision",
                    "Supply a non-empty registry revision string or omit it.",
                )
            )
    if argument_findings:
        phases.append(DiagnosticSet.from_diagnostics(argument_findings, limit=50))

    plan: Plan | None = None
    if invalid_as_of is None:
        try:
            plan = resolve_observation_documents(loaded.documents, as_of=as_of, diagnostic_limit=50)
        except PlanValidationError as error:
            phases.append(error.report.diagnostics)
    combined = _combine_diagnostics(phases, limit=50)
    if combined.error_count:
        raise ProjectValidationError(ValidationReport(combined, loaded.attempted_document_count))
    if plan is None:
        raise RuntimeError("projection validation succeeded without producing a plan")

    if registry_revision is not None:
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
                    DiagnosticSet.from_diagnostics([diagnostic], limit=50),
                    loaded.attempted_document_count,
                )
            )
        plan = plan.model_copy(update={"registry_revision": registry_revision, "plan_digest": None})
        plan = plan.model_copy(update={"plan_digest": canonical_digest(plan)})
    return ProjectResult(plan=plan, sources=_source_provenance(loaded.documents))


def _invalid_as_of_diagnostic(as_of: object) -> Diagnostic | None:
    if isinstance(as_of, datetime):
        try:
            if as_of.utcoffset() is not None:
                return None
        except Exception:
            pass
    return _argument_diagnostic(
        "invalid-as-of",
        "/as_of",
        "as_of",
        "Supply a timezone-aware datetime for deterministic waiver evaluation.",
    )


def _argument_diagnostic(code: str, pointer: str, identity: str, action: str) -> Diagnostic:
    return Diagnostic(
        code=code,
        severity="error",
        message=code.replace("-", " ").capitalize() + ".",
        location=SourceLocation("<input>", pointer),
        identity=identity,
        next_actions=(action,),
    )


def _combine_diagnostics(phases: Sequence[DiagnosticSet], *, limit: int) -> DiagnosticSet:
    retained = sorted(
        (item for phase in phases for item in phase.diagnostics),
        key=lambda item: (
            0 if item.severity == "error" else 1,
            item.code,
            item.location.path,
            item.location.document_index,
            item.location.pointer,
            item.identity or "",
            item.message,
            tuple(sorted(item.next_actions)),
        ),
    )[:limit]
    total_count = sum(phase.total_count for phase in phases)
    error_count = sum(phase.error_count for phase in phases)
    return DiagnosticSet(
        diagnostics=tuple(retained),
        limit=limit,
        total_count=total_count,
        truncated=total_count > limit,
        error_count=error_count,
        warning_count=total_count - error_count,
    )


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
