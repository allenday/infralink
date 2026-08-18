"""Offline-only loading of versioned observation contract documents."""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml
from pydantic import ValidationError

from infralink.observation.canonical import canonical_json
from infralink.observation.diagnostics import Diagnostic, DiagnosticSet, SourceLocation
from infralink.observation.v2 import (
    ObservationV2Document,
    V2InstanceTopologyValidationError,
    V2MetricValidationError,
    V2TopologyValidationError,
    validate_v2_documents,
)

SCHEMA_VERSION = "infralink.observation/v1"
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION, "infralink.observation/v2"})
DEFAULT_DIAGNOSTIC_LIMIT = 100
MAX_SOURCE_BYTES = 4 * 1024 * 1024
MAX_YAML_EVENTS = 100_000
MAX_YAML_DOCUMENTS = 100
MAX_YAML_NESTING_DEPTH = 100

# Identity-bearing top-level collections represented by the versioned source models.
_IDENTITY_COLLECTIONS = frozenset(
    {
        "applications",
        "component_edges",
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
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return an independent mutable copy for downstream validation."""

        return _thaw_mapping(self.data)


@dataclass(frozen=True, slots=True)
class LoadReport:
    """The usable documents and all diagnostics produced while loading them."""

    documents: tuple[ObservationDocument, ...]
    diagnostics: DiagnosticSet
    attempted_document_count: int

    @property
    def valid(self) -> bool:
        return self.diagnostics.error_count == 0


def canonical_parsed_content(data: Mapping[str, Any]) -> bytes:
    """Serialize parsed YAML deterministically for semantic provenance hashing."""

    return canonical_json(_thaw_mapping(data))


def load_observation_documents(
    sources: str | Path | Iterable[str | Path],
    *,
    diagnostic_limit: int = DEFAULT_DIAGNOSTIC_LIMIT,
) -> LoadReport:
    """Load only explicitly supplied YAML files or directories, without ambient I/O."""

    discovered, base, findings = _discover_sources(sources)
    documents: list[ObservationDocument] = []
    attempted_document_count = len(findings)
    for path in discovered:
        source_path = path.relative_to(base).as_posix()
        loaded, load_findings, attempted = _load_file(path, source_path)
        documents.extend(loaded)
        findings.extend(load_findings)
        attempted_document_count += attempted

    findings.extend(_duplicate_id_diagnostics(documents))
    documents, topology_findings = _validate_v2_source_set(documents)
    findings.extend(topology_findings)
    return LoadReport(
        documents=tuple(documents),
        diagnostics=DiagnosticSet.from_diagnostics(findings, limit=diagnostic_limit),
        attempted_document_count=attempted_document_count,
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


def _load_file(
    path: Path, source_path: str
) -> tuple[list[ObservationDocument], list[Diagnostic], int]:
    raw = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    if len(raw) > MAX_SOURCE_BYTES:
        return (
            [],
            [
                Diagnostic(
                    code="yaml-source-too-large",
                    severity="error",
                    message=f"The YAML source exceeds the {MAX_SOURCE_BYTES}-byte limit.",
                    location=SourceLocation(source_path),
                    next_actions=("Split the contract into smaller YAML source files.",),
                )
            ],
            1,
        )
    inspection_finding = _inspect_yaml(raw, source_path)
    if inspection_finding is not None:
        return [], [inspection_finding], _count_attempted_documents(raw)
    try:
        parsed_documents = list(yaml.safe_load_all(raw))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        problem = getattr(error, "problem", None)
        message = "The YAML source is malformed."
        if isinstance(problem, str) and problem:
            message = f"The YAML source is malformed: {problem}."
        return (
            [],
            [
                Diagnostic(
                    code="yaml-malformed",
                    severity="error",
                    message=message,
                    location=SourceLocation(source_path),
                    next_actions=("Repair the YAML syntax and load the source again.",),
                )
            ],
            _count_attempted_documents(raw),
        )

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
                    message=(
                        "The document must declare a supported schema_version: "
                        f"{', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))}."
                    ),
                    location=SourceLocation(source_path, "/schema_version", document_index),
                    next_actions=(f"Add schema_version: {SCHEMA_VERSION}.",),
                )
            )
            continue
        if version not in SUPPORTED_SCHEMA_VERSIONS:
            findings.append(
                Diagnostic(
                    code="schema-version-unsupported",
                    severity="error",
                    message=f"Unsupported observation schema version: {version!r}.",
                    location=SourceLocation(source_path, "/schema_version", document_index),
                    identity=str(version),
                    next_actions=(f"Use one of: {', '.join(sorted(SUPPORTED_SCHEMA_VERSIONS))}.",),
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
        unsupported_value = _find_unsupported_value(parsed)
        if unsupported_value is not None:
            pointer, value_type = unsupported_value
            findings.append(
                Diagnostic(
                    code="canonical-value-unsupported",
                    severity="error",
                    message=f"YAML value type {value_type!r} is outside the canonical domain.",
                    location=SourceLocation(source_path, pointer, document_index),
                    next_actions=(
                        "Use only mappings, lists, strings, integers, finite floats, booleans, or null.",
                    ),
                )
            )
            continue
        if version == "infralink.observation/v2":
            validation_finding = _validate_v2_document_structure(
                parsed, source_path, document_index
            )
            if validation_finding is not None:
                findings.append(validation_finding)
                continue
        semantic_sha256 = hashlib.sha256(canonical_parsed_content(parsed)).hexdigest()
        loaded.append(
            ObservationDocument(
                source_path=source_path,
                data=_freeze_mapping(parsed),
                raw_sha256=raw_sha256,
                semantic_sha256=semantic_sha256,
                document_index=document_index,
                schema_version=version,
            )
        )
    return loaded, findings, len(parsed_documents)


def _validate_v2_document_structure(
    data: dict[str, Any], source_path: str, document_index: int
) -> Diagnostic | None:
    try:
        ObservationV2Document.model_validate_json(json.dumps(data))
    except ValidationError as error:
        message = str(error)
        code = _v2_structure_validation_code(message)
        return Diagnostic(
            code=code,
            severity="error",
            message="The v2 component topology is invalid.",
            location=SourceLocation(source_path, "/", document_index),
            next_actions=(
                "Repair the v2 component, endpoint, and edge references before loading.",
            ),
        )
    return None


def _v2_structure_validation_code(message: str) -> str:
    for fragment, code in (("duplicate component edge id", "duplicate-component-edge-id"),):
        if fragment in message:
            return code
    return "v2-component-topology-invalid"


def _validate_v2_source_set(
    documents: list[ObservationDocument],
) -> tuple[list[ObservationDocument], list[Diagnostic]]:
    v2_documents = [
        document for document in documents if document.schema_version == "infralink.observation/v2"
    ]
    if not v2_documents:
        return documents, []

    parsed = [
        ObservationV2Document.model_validate_json(json.dumps(document.to_dict()))
        for document in v2_documents
    ]
    try:
        validate_v2_documents(parsed)
    except V2TopologyValidationError as error:
        location = _v2_component_edge_location(v2_documents, error.edge_id)
        diagnostic = Diagnostic(
            code=error.code,
            severity="error",
            message="The v2 component topology is invalid.",
            location=location,
            identity=f"component_edges/{error.edge_id}",
            next_actions=("Repair the referenced component endpoint before loading.",),
        )
    except V2InstanceTopologyValidationError as error:
        location = _v2_service_instance_location(
            v2_documents, error.host_id, error.instance_id, error.slot_id
        )
        diagnostic = Diagnostic(
            code=error.code,
            severity="error",
            message="The v2 service instance topology is invalid.",
            location=location,
            identity=f"service_instances/{error.host_id}/{error.instance_id}",
            next_actions=(
                "Repair the referenced service profile or component slot before loading.",
            ),
        )
    except V2MetricValidationError as error:
        location = _v2_metric_error_location(v2_documents, error)
        next_actions = {
            "component-endpoint-binding-unknown-endpoint": (
                "Bind only declared component endpoints at the service instance.",
            ),
            "component-metric-source-endpoint-unbound": (
                "Bind an address for the component metric source endpoint.",
            ),
            "component-metric-binding-unknown-contract": (
                "Bind a metric declared by the selected component profile.",
            ),
        }.get(error.code, ("Bind only labels allowed by the component metric contract.",))
        diagnostic = Diagnostic(
            code=error.code,
            severity="error",
            message="The v2 component metric binding is invalid.",
            location=location,
            identity=(
                f"service_instances/{error.host_id}/{error.instance_id}/{error.component_id}"
            ),
            next_actions=next_actions,
        )
    except ValueError:
        diagnostic = Diagnostic(
            code="v2-component-topology-invalid",
            severity="error",
            message="The v2 component topology is invalid.",
            location=SourceLocation(
                v2_documents[0].source_path, "/", v2_documents[0].document_index
            ),
            next_actions=("Repair the v2 component, profile, and slot references before loading.",),
        )
    else:
        return documents, []
    return [
        document for document in documents if document.schema_version != "infralink.observation/v2"
    ], [diagnostic]


def _v2_component_edge_location(
    documents: list[ObservationDocument], edge_id: str
) -> SourceLocation:
    for document in documents:
        edges = document.data.get("component_edges", ())
        if not isinstance(edges, tuple):
            continue
        for index, edge in enumerate(edges):
            if isinstance(edge, Mapping) and edge.get("id") == edge_id:
                return SourceLocation(
                    document.source_path,
                    f"/component_edges/{index}",
                    document.document_index,
                )
    return SourceLocation(documents[0].source_path, "/", documents[0].document_index)


def _v2_service_instance_location(
    documents: list[ObservationDocument],
    host_id: str,
    instance_id: str,
    slot_id: str | None,
) -> SourceLocation:
    for document in documents:
        instances = document.data.get("service_instances", ())
        if not isinstance(instances, tuple):
            continue
        for instance_index, instance in enumerate(instances):
            if not isinstance(instance, Mapping):
                continue
            if instance.get("host_id") != host_id or instance.get("id") != instance_id:
                continue
            if slot_id is None:
                return SourceLocation(
                    document.source_path,
                    f"/service_instances/{instance_index}/profile_id",
                    document.document_index,
                )
            components = instance.get("components", ())
            if isinstance(components, tuple):
                for component_index, component in enumerate(components):
                    if isinstance(component, Mapping) and component.get("slot_id") == slot_id:
                        return SourceLocation(
                            document.source_path,
                            f"/service_instances/{instance_index}/components/{component_index}/slot_id",
                            document.document_index,
                        )
            return SourceLocation(
                document.source_path,
                f"/service_instances/{instance_index}",
                document.document_index,
            )
    return SourceLocation(documents[0].source_path, "/", documents[0].document_index)


def _v2_metric_error_location(
    documents: list[ObservationDocument], error: V2MetricValidationError
) -> SourceLocation:
    for document in documents:
        instances = document.data.get("service_instances", ())
        if not isinstance(instances, tuple):
            continue
        for instance_index, instance in enumerate(instances):
            if not isinstance(instance, Mapping):
                continue
            if instance.get("host_id") != error.host_id or instance.get("id") != error.instance_id:
                continue
            components = instance.get("components", ())
            if not isinstance(components, tuple):
                break
            for component_index, component in enumerate(components):
                if (
                    not isinstance(component, Mapping)
                    or component.get("slot_id") != error.component_id
                ):
                    continue
                component_pointer = (
                    f"/service_instances/{instance_index}/components/{component_index}"
                )
                if error.location_kind == "component":
                    return SourceLocation(
                        document.source_path,
                        f"{component_pointer}/endpoint_bindings",
                        document.document_index,
                    )
                if error.location_kind == "endpoint-binding":
                    bindings = component.get("endpoint_bindings", ())
                    if isinstance(bindings, tuple):
                        for binding_index, binding in enumerate(bindings):
                            if (
                                isinstance(binding, Mapping)
                                and binding.get("endpoint_id") == error.metric_id
                            ):
                                return SourceLocation(
                                    document.source_path,
                                    f"{component_pointer}/endpoint_bindings/{binding_index}/endpoint_id",
                                    document.document_index,
                                )
                bindings = component.get("metric_bindings", ())
                if not isinstance(bindings, tuple):
                    break
                for binding_index, binding in enumerate(bindings):
                    if isinstance(binding, Mapping) and binding.get("metric_id") == error.metric_id:
                        field = "metric_id" if error.location_kind == "metric-id" else "labels"
                        return SourceLocation(
                            document.source_path,
                            f"{component_pointer}/metric_bindings/{binding_index}/{field}",
                            document.document_index,
                        )
    return _v2_service_instance_location(
        documents, error.host_id, error.instance_id, error.component_id
    )


def _count_attempted_documents(raw: bytes) -> int:
    count = 0
    try:
        for event in yaml.parse(raw):
            if isinstance(event, yaml.events.DocumentStartEvent):
                count += 1
    except (UnicodeDecodeError, yaml.YAMLError):
        pass
    return max(1, count)


def _duplicate_id_diagnostics(documents: Iterable[ObservationDocument]) -> list[Diagnostic]:
    locations: dict[tuple[str, str, str], list[SourceLocation]] = defaultdict(list)
    for document in documents:
        for collection in sorted(_IDENTITY_COLLECTIONS):
            objects = document.data.get(collection)
            if not isinstance(objects, tuple):
                continue
            for index, item in enumerate(objects):
                if isinstance(item, Mapping) and isinstance(item.get("id"), str):
                    object_id = item["id"]
                    if collection == "service_instances":
                        host_id = item.get("host_id")
                        if not isinstance(host_id, str):
                            continue
                        object_id = f"{host_id}/{object_id}"
                    locations[(document.schema_version, collection, object_id)].append(
                        SourceLocation(
                            document.source_path,
                            f"/{collection}/{index}/id",
                            document.document_index,
                        )
                    )

    findings: list[Diagnostic] = []
    for (_, collection, object_id), occurrences in locations.items():
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


def _find_unsupported_value(value: Any, pointer: str = "/") -> tuple[str, str] | None:
    if value is None or type(value) in (str, int, bool):
        return None
    if type(value) is float:
        return None if math.isfinite(value) else (pointer, "non-finite-float")
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_pointer = (
                f"/{_escape_pointer_token(key)}"
                if pointer == "/"
                else f"{pointer}/{_escape_pointer_token(key)}"
            )
            invalid = _find_unsupported_value(child, child_pointer)
            if invalid is not None:
                return invalid
        return None
    if isinstance(value, list):
        for index, child in enumerate(value):
            child_pointer = f"/{index}" if pointer == "/" else f"{pointer}/{index}"
            invalid = _find_unsupported_value(child, child_pointer)
            if invalid is not None:
                return invalid
        return None
    return pointer, type(value).__name__


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
