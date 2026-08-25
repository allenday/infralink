"""Typed, offline public API for observation validation and projection."""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from infralink.observation.canonical import canonical_digest
from infralink.observation.codes import V2_DIAGNOSTIC_CODES
from infralink.observation.diagnostics import Diagnostic, DiagnosticSet, SourceLocation
from infralink.observation.loader import ObservationDocument, load_observation_documents
from infralink.observation.planner import Plan, PlanValidationError, resolve_observation_documents
from infralink.observation.v2 import (
    ObservationV2Document,
    PlannedArtifactBinding,
    PlannedConfigurationBinding,
    PlannedMetricContract,
    plan_v2_artifact_bindings,
    plan_v2_configuration_bindings,
    plan_v2_metric_contracts,
)

V1_SCHEMA_VERSION = "infralink.observation/v1"
V2_SCHEMA_VERSION = "infralink.observation/v2"


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


@dataclass(frozen=True, slots=True)
class V2MetricProjectResult:
    """One typed V2 metric projection with source provenance."""

    metrics: tuple[PlannedMetricContract, ...]
    sources: tuple[SourceProvenance, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "metrics": [metric.model_dump(mode="json") for metric in self.metrics],
            "sources": [asdict(source) for source in self.sources],
        }


@dataclass(frozen=True, slots=True)
class V2ConfigurationProjectResult:
    """One typed V2 configuration projection with source provenance."""

    configuration_bindings: tuple[PlannedConfigurationBinding, ...]
    sources: tuple[SourceProvenance, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "configuration_bindings": [
                binding.model_dump(mode="json") for binding in self.configuration_bindings
            ],
            "sources": [asdict(source) for source in self.sources],
        }


@dataclass(frozen=True, slots=True)
class V2ArtifactProjectResult:
    """One typed V2 artifact projection with source provenance."""

    artifact_bindings: tuple[PlannedArtifactBinding, ...]
    sources: tuple[SourceProvenance, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "artifact_bindings": [
                binding.model_dump(mode="json") for binding in self.artifact_bindings
            ],
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
        selected_version, selection_diagnostic = _select_validation_schema(
            loaded.documents, loaded.diagnostics
        )
        if selection_diagnostic is not None:
            phases.append(DiagnosticSet.from_diagnostics([selection_diagnostic], limit=limit))
        elif selected_version == V1_SCHEMA_VERSION:
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
        elif registry_revision is not None and registry_revision.strip():
            phases.append(
                DiagnosticSet.from_diagnostics([_v2_registry_revision_diagnostic()], limit=limit)
            )
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


def project_v2_metric_contracts(paths: Sequence[Path], *, limit: int = 50) -> V2MetricProjectResult:
    """Load explicit V2 sources and produce only normalized metric contracts.

    This is an offline boundary for generic adapter consumers. It accepts no
    rendered artifacts, binding documents, or provider configuration.
    """

    loaded = load_observation_documents(paths, diagnostic_limit=limit)
    version_findings: list[Diagnostic] = []
    documents: list[ObservationV2Document] = []
    provenance: list[ObservationDocument] = []
    for document in loaded.documents:
        if document.schema_version != "infralink.observation/v2":
            version_findings.append(
                Diagnostic(
                    code="v2-metric-source-version-invalid",
                    severity="error",
                    message="V2 metric projection accepts only infralink.observation/v2 sources.",
                    location=SourceLocation(
                        document.source_path,
                        "/schema_version",
                        document.document_index,
                    ),
                    identity=document.schema_version,
                    next_actions=("Supply only infralink.observation/v2 source documents.",),
                )
            )
            continue
        documents.append(ObservationV2Document.model_validate_json(json.dumps(document.to_dict())))
        provenance.append(document)
    if not documents and not version_findings and not loaded.diagnostics.error_count:
        version_findings.append(
            _argument_diagnostic(
                "no-usable-v2-metric-document",
                "/sources",
                "sources",
                "Supply at least one infralink.observation/v2 source document.",
            )
        )
    combined = _combine_diagnostics(
        [
            loaded.diagnostics,
            DiagnosticSet.from_diagnostics(version_findings, limit=limit),
        ],
        limit=limit,
    )
    if combined.error_count:
        raise ProjectValidationError(ValidationReport(combined, loaded.attempted_document_count))
    return V2MetricProjectResult(
        metrics=plan_v2_metric_contracts(documents),
        sources=_source_provenance(tuple(provenance)),
    )


def project_v2_configuration_bindings(
    paths: Sequence[Path], *, limit: int = 50
) -> V2ConfigurationProjectResult:
    """Load explicit V2 sources and produce only normalized configuration bindings."""

    loaded = load_observation_documents(paths, diagnostic_limit=limit)
    version_findings: list[Diagnostic] = []
    documents: list[ObservationV2Document] = []
    provenance: list[ObservationDocument] = []
    for document in loaded.documents:
        if document.schema_version != "infralink.observation/v2":
            version_findings.append(
                Diagnostic(
                    code="v2-configuration-source-version-invalid",
                    severity="error",
                    message="V2 configuration projection accepts only infralink.observation/v2 sources.",
                    location=SourceLocation(
                        document.source_path,
                        "/schema_version",
                        document.document_index,
                    ),
                    identity=document.schema_version,
                    next_actions=("Supply only infralink.observation/v2 source documents.",),
                )
            )
            continue
        documents.append(ObservationV2Document.model_validate_json(json.dumps(document.to_dict())))
        provenance.append(document)
    if not documents and not version_findings and not loaded.diagnostics.error_count:
        version_findings.append(
            _argument_diagnostic(
                "no-usable-v2-configuration-document",
                "/sources",
                "sources",
                "Supply at least one infralink.observation/v2 source document.",
            )
        )
    combined = _combine_diagnostics(
        [
            loaded.diagnostics,
            DiagnosticSet.from_diagnostics(version_findings, limit=limit),
        ],
        limit=limit,
    )
    if combined.error_count:
        raise ProjectValidationError(ValidationReport(combined, loaded.attempted_document_count))
    return V2ConfigurationProjectResult(
        configuration_bindings=plan_v2_configuration_bindings(documents),
        sources=_source_provenance(tuple(provenance)),
    )


def project_v2_artifact_bindings(
    paths: Sequence[Path], *, limit: int = 50
) -> V2ArtifactProjectResult:
    """Load explicit V2 sources and produce only normalized artifact bindings."""

    loaded = load_observation_documents(paths, diagnostic_limit=limit)
    version_findings: list[Diagnostic] = []
    documents: list[ObservationV2Document] = []
    provenance: list[ObservationDocument] = []
    for document in loaded.documents:
        if document.schema_version != "infralink.observation/v2":
            version_findings.append(
                Diagnostic(
                    code="v2-artifact-source-version-invalid",
                    severity="error",
                    message="V2 artifact projection accepts only infralink.observation/v2 sources.",
                    location=SourceLocation(
                        document.source_path,
                        "/schema_version",
                        document.document_index,
                    ),
                    identity=document.schema_version,
                    next_actions=("Supply only infralink.observation/v2 source documents.",),
                )
            )
            continue
        documents.append(ObservationV2Document.model_validate_json(json.dumps(document.to_dict())))
        provenance.append(document)
    if not documents and not version_findings and not loaded.diagnostics.error_count:
        version_findings.append(
            _argument_diagnostic(
                "no-usable-v2-artifact-document",
                "/sources",
                "sources",
                "Supply at least one infralink.observation/v2 source document.",
            )
        )
    combined = _combine_diagnostics(
        [
            loaded.diagnostics,
            DiagnosticSet.from_diagnostics(version_findings, limit=limit),
        ],
        limit=limit,
    )
    if combined.error_count:
        raise ProjectValidationError(ValidationReport(combined, loaded.attempted_document_count))
    return V2ArtifactProjectResult(
        artifact_bindings=plan_v2_artifact_bindings(documents),
        sources=_source_provenance(tuple(provenance)),
    )


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


def _v2_registry_revision_diagnostic() -> Diagnostic:
    return Diagnostic(
        code="v2-registry-revision-unsupported",
        severity="error",
        message="V2 validation does not accept registry_revision until V2 declares one.",
        location=SourceLocation("<input>", "/registry_revision"),
        identity="registry_revision",
        next_actions=(
            "Omit registry_revision for v2 validation until the source declares an authoritative binding.",
        ),
    )


def _select_validation_schema(
    documents: tuple[ObservationDocument, ...], diagnostics: DiagnosticSet
) -> tuple[str | None, Diagnostic | None]:
    """Select one declared schema version for validation without coercion."""

    versions = {document.schema_version for document in documents}
    if V1_SCHEMA_VERSION in versions and V2_SCHEMA_VERSION in versions:
        selected = next(
            document for document in documents if document.schema_version == V2_SCHEMA_VERSION
        )
        return None, Diagnostic(
            code="mixed-observation-schema-versions",
            severity="error",
            message="Validate one declared observation schema version at a time.",
            location=SourceLocation(
                selected.source_path, "/schema_version", selected.document_index
            ),
            identity=V2_SCHEMA_VERSION,
            next_actions=("Split v1 and v2 declarations into separate validation invocations.",),
        )
    if V2_SCHEMA_VERSION in versions:
        return V2_SCHEMA_VERSION, None
    if V1_SCHEMA_VERSION not in versions and any(
        diagnostic.code in V2_DIAGNOSTIC_CODES for diagnostic in diagnostics
    ):
        return V2_SCHEMA_VERSION, None
    return V1_SCHEMA_VERSION, None


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
    "V2ConfigurationProjectResult",
    "V2MetricProjectResult",
    "project",
    "project_v2_configuration_bindings",
    "project_v2_metric_contracts",
    "validate",
]
