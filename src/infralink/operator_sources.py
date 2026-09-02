"""Typed registry and edge source selection for operator operations."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

import yaml
from agent_surface import OperationError
from pydantic import BaseModel, ConfigDict, Field

from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.operator_config import OperatorConfigError, configured_registry

REGISTRY_COMPANION_SCAN_MAX_ENTRIES = 10_000


def managed_runtime_registry_root() -> Path:
    """Return the sole deployed Registry cache, which is never an authoring tree."""
    return Path("/var/lib/infralink/registry")


class OperatorInputs(BaseModel):
    """Root inputs projected once before every Agent Surface command path."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    registry: Path | None = Field(
        default=None,
        description="Registry checkout root; defaults to local operator configuration.",
    )
    edges: Path | None = Field(
        default=None,
        description="Optional edge declaration file; defaults to the registry companion file.",
    )


class SourceRequest(OperatorInputs):
    """Explicit source inputs inherited by every registry-backed operation."""


class LoadedSources(BaseModel):
    """Resolved paths and parsed declarations supplied to operation handlers."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    registry_path: Path
    registry_source_path: Path
    edges_path: Path
    edges_source_path: Path
    registry: Registry
    edges: EdgeSet


class LoadedRegistry(BaseModel):
    """Resolved registry source supplied to operations that do not need edges."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    registry_path: Path
    registry_source_path: Path
    registry: Registry


def load_registry(request: SourceRequest) -> LoadedRegistry:
    """Load the one explicitly selected registry checkout."""
    try:
        selected = request.registry or configured_registry()
    except OperatorConfigError as error:
        raise OperationError(
            "input_load_failed",
            "Operator configuration could not be loaded",
            details=({"source": "operator_config", "path": str(error)},),
            fix="Correct INFRALINK_CONFIG or pass an explicit registry checkout root.",
        ) from None
    if selected is None:
        raise OperationError(
            "configuration_required",
            "Registry source is required",
            details=({"source": "registry"},),
            fix="Pass a registry checkout root or configure INFRALINK_CONFIG.",
        )
    registry_source_path = selected.expanduser()
    registry_path = registry_source_path.resolve()
    if not registry_path.exists():
        raise OperationError(
            "source_not_found",
            "Registry source does not exist",
            details=({"source": "registry", "path": str(registry_path)},),
            fix="Pass a readable registry checkout root.",
        )
    if not registry_path.is_dir() or registry_path.name == "hosts":
        raise OperationError(
            "source_invalid",
            "Registry source must be the checkout root",
            details=({"source": "registry", "path": str(registry_path)},),
            fix="Pass the repository root, not a registry YAML file or its hosts subdirectory.",
        )

    try:
        registry = Registry.load_dir(_hosts_root(registry_path))
    except Exception as error:
        raise OperationError(
            "source_invalid",
            "Registry source could not be loaded",
            details=({"source": "registry", "path": str(registry_path)},),
            fix="Correct the registry declaration and validate it before retrying.",
        ) from error

    return LoadedRegistry(
        registry_path=registry_path,
        registry_source_path=registry_source_path,
        registry=registry,
    )


def load_sources(request: SourceRequest) -> LoadedSources:
    """Load the selected registry checkout and its edge declaration."""
    loaded_registry = load_registry(request)
    environment_edges = os.environ.get("INFRALINK_EDGES")
    edges_source_path = (
        request.edges.expanduser()
        if request.edges is not None
        else Path(environment_edges).expanduser()
        if environment_edges
        else resolve_registry_companion(loaded_registry.registry_source_path, filename="edges.yml")
    )
    edges_path = edges_source_path.resolve()
    if not edges_path.is_file():
        raise OperationError(
            "source_not_found",
            "Edge declaration source does not exist",
            details=({"source": "edges", "path": str(edges_path)},),
            fix="Pass --edges with a readable edge declaration file.",
        )
    try:
        edges = EdgeSet.load(edges_path)
    except Exception as error:
        raise OperationError(
            "source_invalid",
            "Edge declaration source could not be loaded",
            details=({"source": "edges", "path": str(edges_path)},),
            fix="Correct the edge declaration and validate it before retrying.",
        ) from error
    return LoadedSources(
        registry_path=loaded_registry.registry_path,
        registry_source_path=loaded_registry.registry_source_path,
        edges_path=edges_path,
        edges_source_path=edges_source_path,
        registry=loaded_registry.registry,
        edges=edges,
    )


def load_info_sources(request: SourceRequest) -> LoadedSources:
    """Load info's established checkout-root or explicit legacy YAML inputs.

    ``info`` predates the checkout-only operator read surface and remains a
    declared-summary command. Keeping its documented YAML input form here
    avoids a transport migration changing selection semantics for operators.
    Other typed reads continue to require a checkout root through
    :func:`load_sources`.
    """
    try:
        selected = request.registry or configured_registry()
    except OperatorConfigError as error:
        raise OperationError(
            "input_load_failed",
            "Operator configuration could not be loaded",
            details=({"source": "operator_config", "path": str(error)},),
            fix="Correct INFRALINK_CONFIG or pass an explicit registry source.",
        ) from None
    if selected is None:
        raise OperationError(
            "configuration_required",
            "Registry source is required",
            details=({"source": "registry"},),
            fix="Pass a registry checkout root or YAML source, or configure INFRALINK_CONFIG.",
        )
    registry_source_path = selected.expanduser()
    registry_path = registry_source_path.resolve()
    if registry_path.is_dir():
        return load_sources(request)
    if not registry_path.is_file():
        raise OperationError(
            "input_load_failed",
            "Registry source could not be loaded",
            details=({"source": "registry", "path": str(registry_source_path)},),
            fix="Pass a readable registry checkout root or YAML source.",
        )
    try:
        registry = Registry.load(registry_path)
    except Exception as error:
        raise OperationError(
            "input_load_failed",
            "Registry source could not be loaded",
            details=({"source": "registry", "path": str(registry_source_path)},),
            fix="Correct the registry declaration and validate it before retrying.",
        ) from error

    if request.edges is not None:
        edges_source_path = request.edges.expanduser()
        edges_path = edges_source_path.resolve()
        if not edges_path.is_file():
            raise OperationError(
                "input_load_failed",
                "Edge declaration source could not be loaded",
                details=({"source": "edges", "path": str(edges_source_path)},),
                fix="Pass a readable edge declaration source.",
            )
        try:
            edges = EdgeSet.load(edges_path)
        except Exception as error:
            raise OperationError(
                "input_load_failed",
                "Edge declaration source could not be loaded",
                details=({"source": "edges", "path": str(edges_source_path)},),
                fix="Correct the edge declaration and validate it before retrying.",
            ) from error
    else:
        try:
            edges = EdgeSet.from_registry(yaml.safe_load(registry_path.read_text(encoding="utf-8")))
        except Exception as error:
            raise OperationError(
                "input_load_failed",
                "Registry source could not be loaded",
                details=({"source": "registry", "path": str(registry_source_path)},),
                fix="Correct the registry declaration and validate it before retrying.",
            ) from error
        # The legacy document contains the implicit edge declaration too.
        edges_source_path = registry_source_path
        edges_path = registry_path

    return LoadedSources(
        registry_path=registry_path,
        registry_source_path=registry_source_path,
        edges_path=edges_path,
        edges_source_path=edges_source_path,
        registry=registry,
        edges=edges,
    )


def resolve_registry_companion(
    root: Path,
    *,
    filename: str | None,
    source: str = "edges",
    predicate: Callable[[Path], bool] | None = None,
    unique_by_parent: bool = False,
) -> Path:
    """Resolve one bounded companion declaration from a Registry checkout."""
    source_label = "edge" if source == "edges" else source.replace("_", " ")
    candidates: list[Path] = []
    candidate_keys: set[Path] = set()
    inspected = 0
    pending = [root]
    while pending:
        directory = pending.pop()
        inspected += 1
        if inspected > REGISTRY_COMPANION_SCAN_MAX_ENTRIES:
            raise _companion_scan_limit_error(root, source, filename)
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    inspected += 1
                    if inspected > REGISTRY_COMPANION_SCAN_MAX_ENTRIES:
                        raise _companion_scan_limit_error(root, source, filename)
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name != ".git":
                            pending.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    candidate = Path(entry.path)
                    if (filename is None or entry.name == filename) and (
                        predicate is None or predicate(candidate)
                    ):
                        key = candidate.parent if unique_by_parent else candidate
                        if key in candidate_keys:
                            continue
                        candidate_keys.add(key)
                        candidates.append(candidate)
                        if len(candidates) == 2:
                            break
        except OSError:
            raise _companion_scan_error(root, source, filename) from None
        if len(candidates) == 2:
            break
    if len(candidates) == 1:
        return candidates[0]
    if not candidates:
        raise OperationError(
            "configuration_required",
            f"Registry checkout has no {source_label} declaration",
            details=(
                {
                    "source": source,
                    "registry": str(root),
                    "filename": filename,
                    "reason": "missing",
                },
            ),
            fix=(
                f"Pass --{source.replace('_', '-')} with the declaration path or add exactly one "
                f"{filename or 'matching declaration'} to the registry checkout."
            ),
        )
    raise OperationError(
        "configuration_required",
        f"Registry checkout has ambiguous {source_label} declarations",
        details=(
            {"source": source, "registry": str(root), "filename": filename, "reason": "ambiguous"},
        ),
        fix=(
            "Pass --edges with the intended edge declaration path."
            if source == "edges"
            else f"Pass --{source.replace('_', '-')} with the intended declaration path."
        ),
    )


def _companion_scan_limit_error(root: Path, source: str, filename: str | None) -> OperationError:
    return OperationError(
        "configuration_required",
        "Registry companion scan exceeded its fixed entry limit",
        details=(
            {
                "source": source,
                "registry": str(root),
                "filename": filename,
                "reason": "scan_limit_exceeded",
            },
        ),
        fix=f"Pass --{source.replace('_', '-')} with the declaration path.",
    )


def _companion_scan_error(root: Path, source: str, filename: str | None) -> OperationError:
    return OperationError(
        "configuration_required",
        "Registry companion source could not be scanned",
        details=(
            {
                "source": source,
                "registry": str(root),
                "filename": filename,
                "reason": "scan_failed",
            },
        ),
        fix=f"Pass --{source.replace('_', '-')} with the declaration path.",
    )


def _hosts_root(registry_path: Path) -> Path:
    nested_hosts = registry_path / "hosts"
    if not nested_hosts.is_dir():
        raise OperationError(
            "source_invalid",
            "Registry checkout has no hosts directory",
            details=({"source": "registry", "path": str(registry_path)},),
            fix="Pass a registry checkout root containing hosts/.",
        )
    return nested_hosts
