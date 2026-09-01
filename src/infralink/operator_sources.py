"""Typed registry and edge source selection for operator operations."""

from __future__ import annotations

from pathlib import Path

from agent_surface import OperationError
from pydantic import BaseModel, ConfigDict, Field

from infralink.core.edges import EdgeSet
from infralink.core.registry import Registry
from infralink.operator_config import OperatorConfigError, configured_registry


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
    edges_source_path = (
        request.edges.expanduser()
        if request.edges is not None
        else loaded_registry.registry_source_path / "network/main-dev/edges/edges.yml"
    )
    edges_path = edges_source_path.resolve()
    if not edges_path.is_file():
        raise OperationError(
            "source_not_found",
            "Edge declaration source does not exist",
            details=({"source": "edges", "path": str(edges_path)},),
            fix="Pass --edges or provide network/main-dev/edges/edges.yml in the registry checkout.",
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
