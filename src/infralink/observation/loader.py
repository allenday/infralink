"""Offline-only loading of versioned observation contract documents."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from infralink.observation.diagnostics import Diagnostic, DiagnosticSet, SourceLocation

SCHEMA_VERSION = "infralink.observation/v1"
DEFAULT_DIAGNOSTIC_LIMIT = 100

# Identity-bearing top-level collections represented by the v1 source models.
_IDENTITY_COLLECTIONS = frozenset(
    {
        "applications",
        "datasource_bindings",
        "dependency_contracts",
        "observation_backends",
        "operations_views",
        "provider_aliases",
        "readiness_suites",
        "renderer_binding_identities",
        "renderer_bindings",
        "secret_bindings",
        "service_instances",
        "service_profiles",
        "waivers",
    }
)


@dataclass(frozen=True, slots=True)
class ObservationDocument:
    """A parsed source document with byte-level and semantic provenance."""

    source_path: str
    data: Mapping[str, Any]
    raw_sha256: str
    semantic_sha256: str


@dataclass(frozen=True, slots=True)
class LoadReport:
    """The usable documents and all diagnostics produced while loading them."""

    documents: tuple[ObservationDocument, ...]
    diagnostics: DiagnosticSet

    @property
    def valid(self) -> bool:
        return not any(item.severity == "error" for item in self.diagnostics)


def canonical_parsed_content(data: Mapping[str, Any]) -> bytes:
    """Serialize parsed YAML deterministically for semantic provenance hashing."""

    return yaml.safe_dump(
        dict(data),
        allow_unicode=True,
        canonical=True,
        sort_keys=True,
    ).encode("utf-8")


def load_observation_documents(
    sources: str | Path | Iterable[str | Path],
    *,
    diagnostic_limit: int = DEFAULT_DIAGNOSTIC_LIMIT,
) -> LoadReport:
    """Load only explicitly supplied YAML files or directories, without ambient I/O."""

    discovered, base, findings = _discover_sources(sources)
    documents: list[ObservationDocument] = []
    for path in discovered:
        source_path = path.relative_to(base).as_posix()
        loaded, load_findings = _load_file(path, source_path)
        documents.extend(loaded)
        findings.extend(load_findings)

    findings.extend(_duplicate_id_diagnostics(documents))
    return LoadReport(
        documents=tuple(documents),
        diagnostics=DiagnosticSet.from_diagnostics(findings, limit=diagnostic_limit),
    )


def _discover_sources(
    sources: str | Path | Iterable[str | Path],
) -> tuple[list[Path], Path, list[Diagnostic]]:
    supplied_paths = (
        [Path(sources)] if isinstance(sources, (str, Path)) else [Path(p) for p in sources]
    )
    supplied = [path.absolute() for path in supplied_paths]
    findings: list[Diagnostic] = []
    discovered: set[Path] = set()

    for source in supplied:
        if source.is_dir():
            discovered.update(
                path
                for path in source.rglob("*")
                if path.is_file() and path.suffix in {".yml", ".yaml"}
            )
        elif source.is_file():
            discovered.add(source)
        else:
            findings.append(
                Diagnostic(
                    code="source-not-found",
                    severity="error",
                    message="The supplied observation source does not exist.",
                    location=SourceLocation(source.as_posix()),
                    next_actions=("Supply an existing YAML file or directory.",),
                )
            )

    if len(supplied) == 1 and supplied[0].is_dir():
        base = supplied[0]
    elif discovered:
        parents = [str(path.parent.absolute()) for path in discovered]
        base = Path(_common_path(parents))
    else:
        base = Path.cwd()
    ordered = sorted(discovered, key=lambda path: path.relative_to(base).as_posix())
    return ordered, base, findings


def _common_path(paths: list[str]) -> str:
    # pathlib has no common-path operation; importing os.path does not inspect the environment.
    from os.path import commonpath

    return commonpath(paths)


def _load_file(path: Path, source_path: str) -> tuple[list[ObservationDocument], list[Diagnostic]]:
    raw = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    try:
        parsed_documents = list(yaml.safe_load_all(raw))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        problem = getattr(error, "problem", None)
        message = "The YAML source is malformed."
        if isinstance(problem, str) and problem:
            message = f"The YAML source is malformed: {problem}."
        return [], [
            Diagnostic(
                code="yaml-malformed",
                severity="error",
                message=message,
                location=SourceLocation(source_path),
                next_actions=("Repair the YAML syntax and load the source again.",),
            )
        ]

    loaded: list[ObservationDocument] = []
    findings: list[Diagnostic] = []
    for data in parsed_documents:
        if not isinstance(data, Mapping):
            findings.append(
                Diagnostic(
                    code="document-root-not-mapping",
                    severity="error",
                    message="An observation document root must be a mapping.",
                    location=SourceLocation(source_path),
                    next_actions=("Replace the document root with a YAML mapping.",),
                )
            )
            continue
        version = data.get("schema_version")
        if version is None:
            findings.append(
                Diagnostic(
                    code="schema-version-missing",
                    severity="error",
                    message=f"The document must declare schema_version {SCHEMA_VERSION!r}.",
                    location=SourceLocation(source_path, "/schema_version"),
                    next_actions=(f"Add schema_version: {SCHEMA_VERSION}.",),
                )
            )
            continue
        if version != SCHEMA_VERSION:
            findings.append(
                Diagnostic(
                    code="schema-version-unsupported",
                    severity="error",
                    message=f"Unsupported observation schema version: {version!r}.",
                    location=SourceLocation(source_path, "/schema_version"),
                    identity=str(version),
                    next_actions=(f"Use schema_version: {SCHEMA_VERSION}.",),
                )
            )
            continue
        parsed = dict(data)
        loaded.append(
            ObservationDocument(
                source_path=source_path,
                data=parsed,
                raw_sha256=raw_sha256,
                semantic_sha256=hashlib.sha256(canonical_parsed_content(parsed)).hexdigest(),
            )
        )
    return loaded, findings


def _duplicate_id_diagnostics(documents: Iterable[ObservationDocument]) -> list[Diagnostic]:
    locations: dict[tuple[str, str], list[SourceLocation]] = defaultdict(list)
    for document in documents:
        for collection in sorted(_IDENTITY_COLLECTIONS):
            objects = document.data.get(collection)
            if not isinstance(objects, list):
                continue
            for index, item in enumerate(objects):
                if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                    locations[(collection, item["id"])].append(
                        SourceLocation(document.source_path, f"/{collection}/{index}/id")
                    )

    findings: list[Diagnostic] = []
    for (collection, object_id), occurrences in locations.items():
        if len(occurrences) < 2:
            continue
        identity = f"{collection}/{object_id}"
        for location in occurrences:
            findings.append(
                Diagnostic(
                    code="duplicate-object-id",
                    severity="error",
                    message=f"Object id {object_id!r} is duplicated in {collection!r}.",
                    location=location,
                    identity=identity,
                    next_actions=(f"Give every object in {collection!r} a unique id.",),
                )
            )
    return findings
