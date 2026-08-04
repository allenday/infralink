"""Offline-only loading of versioned observation contract documents."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

from infralink.observation.diagnostics import Diagnostic, DiagnosticSet, SourceLocation

SCHEMA_VERSION = "infralink.observation/v1"
DEFAULT_DIAGNOSTIC_LIMIT = 100
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_YAML_EVENTS = 100_000
MAX_YAML_DOCUMENTS = 100
MAX_YAML_NESTING_DEPTH = 100

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
    document_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return an independent mutable copy for downstream validation."""

        return _thaw_mapping(self.data)


@dataclass(frozen=True, slots=True)
class LoadReport:
    """The usable documents and all diagnostics produced while loading them."""

    documents: tuple[ObservationDocument, ...]
    diagnostics: DiagnosticSet

    @property
    def valid(self) -> bool:
        return self.diagnostics.error_count == 0


def canonical_parsed_content(data: Mapping[str, Any]) -> bytes:
    """Serialize parsed YAML deterministically for semantic provenance hashing."""

    return yaml.safe_dump(
        _thaw_mapping(data),
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
            if source.suffix in {".yml", ".yaml"}:
                discovered.add(source)
            else:
                findings.append(
                    Diagnostic(
                        code="unsupported-source-extension",
                        severity="error",
                        message="Observation source files must use .yml or .yaml.",
                        location=SourceLocation(source.name),
                        next_actions=("Supply a .yml or .yaml observation source file.",),
                    )
                )
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
    if len(raw) > MAX_SOURCE_BYTES:
        return [], [
            Diagnostic(
                code="yaml-source-too-large",
                severity="error",
                message=f"The YAML source exceeds the {MAX_SOURCE_BYTES}-byte limit.",
                location=SourceLocation(source_path),
                next_actions=("Split the contract into smaller YAML source files.",),
            )
        ]
    inspection_finding = _inspect_yaml(raw, source_path)
    if inspection_finding is not None:
        return [], [inspection_finding]
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
    for document_index, data in enumerate(parsed_documents):
        if not isinstance(data, Mapping):
            findings.append(
                Diagnostic(
                    code="document-root-not-mapping",
                    severity="error",
                    message="An observation document root must be a mapping.",
                    location=SourceLocation(source_path, document_index=document_index),
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
                    location=SourceLocation(source_path, "/schema_version", document_index),
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
                    location=SourceLocation(source_path, "/schema_version", document_index),
                    identity=str(version),
                    next_actions=(f"Use schema_version: {SCHEMA_VERSION}.",),
                )
            )
            continue
        parsed = dict(data)
        invalid_key_pointer = _find_non_string_key(parsed)
        if invalid_key_pointer is not None:
            findings.append(
                Diagnostic(
                    code="mapping-key-not-string",
                    severity="error",
                    message="YAML contract mapping keys must be strings.",
                    location=SourceLocation(source_path, invalid_key_pointer, document_index),
                    next_actions=("Replace the non-string mapping key with a string key.",),
                )
            )
            continue
        semantic_sha256 = hashlib.sha256(canonical_parsed_content(parsed)).hexdigest()
        loaded.append(
            ObservationDocument(
                source_path=source_path,
                data=_freeze_mapping(parsed),
                raw_sha256=raw_sha256,
                semantic_sha256=semantic_sha256,
                document_index=document_index,
            )
        )
    return loaded, findings


def _duplicate_id_diagnostics(documents: Iterable[ObservationDocument]) -> list[Diagnostic]:
    locations: dict[tuple[str, str], list[SourceLocation]] = defaultdict(list)
    for document in documents:
        for collection in sorted(_IDENTITY_COLLECTIONS):
            objects = document.data.get(collection)
            if not isinstance(objects, tuple):
                continue
            for index, item in enumerate(objects):
                if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                    locations[(collection, item["id"])].append(
                        SourceLocation(
                            document.source_path,
                            f"/{collection}/{index}/id",
                            document.document_index,
                        )
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


def _inspect_yaml(raw: bytes, source_path: str) -> Diagnostic | None:
    document_index = 0
    document_count = 0
    nesting_depth = 0
    try:
        for event_count, event in enumerate(yaml.parse(raw), start=1):
            if event_count > MAX_YAML_EVENTS:
                return Diagnostic(
                    code="yaml-source-too-complex",
                    severity="error",
                    message=f"The YAML source exceeds the {MAX_YAML_EVENTS}-event limit.",
                    location=SourceLocation(source_path, document_index=document_index),
                    next_actions=("Split the contract into simpler YAML source files.",),
                )
            if isinstance(event, yaml.events.DocumentStartEvent):
                document_index = document_count
                document_count += 1
                if document_count > MAX_YAML_DOCUMENTS:
                    return Diagnostic(
                        code="yaml-too-many-documents",
                        severity="error",
                        message=f"The YAML source exceeds the {MAX_YAML_DOCUMENTS}-document limit.",
                        location=SourceLocation(source_path, document_index=document_index),
                        next_actions=("Split the documents across multiple YAML source files.",),
                    )
            if isinstance(event, (yaml.events.MappingStartEvent, yaml.events.SequenceStartEvent)):
                nesting_depth += 1
                if nesting_depth > MAX_YAML_NESTING_DEPTH:
                    return Diagnostic(
                        code="yaml-nesting-too-deep",
                        severity="error",
                        message=(
                            "The YAML source exceeds the "
                            f"{MAX_YAML_NESTING_DEPTH}-level nesting limit."
                        ),
                        location=SourceLocation(source_path, document_index=document_index),
                        next_actions=("Flatten deeply nested contract content.",),
                    )
            elif isinstance(event, (yaml.events.MappingEndEvent, yaml.events.SequenceEndEvent)):
                nesting_depth -= 1
            if isinstance(event, yaml.events.AliasEvent) or getattr(event, "anchor", None):
                return Diagnostic(
                    code="yaml-alias-forbidden",
                    severity="error",
                    message="YAML anchors and aliases are not allowed in observation contracts.",
                    location=SourceLocation(source_path, document_index=document_index),
                    next_actions=("Expand the aliased value directly and remove anchors.",),
                )
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        problem = getattr(error, "problem", None)
        message = "The YAML source is malformed."
        if isinstance(problem, str) and problem:
            message = f"The YAML source is malformed: {problem}."
        return Diagnostic(
            code="yaml-malformed",
            severity="error",
            message=message,
            location=SourceLocation(source_path, document_index=document_index),
            next_actions=("Repair the YAML syntax and load the source again.",),
        )
    return None


def _find_non_string_key(value: Any, pointer: str = "/") -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if not isinstance(key, str):
                return pointer
            child_pointer = (
                f"/{_escape_pointer_token(key)}"
                if pointer == "/"
                else f"{pointer}/{_escape_pointer_token(key)}"
            )
            invalid = _find_non_string_key(child, child_pointer)
            if invalid is not None:
                return invalid
    elif isinstance(value, list):
        for index, child in enumerate(value):
            child_pointer = f"/{index}" if pointer == "/" else f"{pointer}/{index}"
            invalid = _find_non_string_key(child, child_pointer)
            if invalid is not None:
                return invalid
    return None


def _escape_pointer_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _freeze_mapping(data: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_value(value) for key, value in data.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, set):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _thaw_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    return {key: _thaw_value(value) for key, value in data.items()}


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _thaw_mapping(value)
    if isinstance(value, tuple):
        return [_thaw_value(item) for item in value]
    if isinstance(value, frozenset):
        return {_thaw_value(item) for item in value}
    return value
