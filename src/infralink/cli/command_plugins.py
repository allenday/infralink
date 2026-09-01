"""Manifest-backed public commands supplied by installed Infralink packages."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from importlib.metadata import EntryPoint, entry_points
from typing import Any

import click
from agent_surface import App
from agent_surface.manifest import ManifestMismatch, installed_manifests, verify_manifest

from infralink.agent_surface import mounted_click_command

_GROUP = "infralink.commands"


@dataclass
class _DiscoveryCache:
    catalog: dict[str, tuple[Mapping[str, Any], EntryPoint]] | None = None


_DISCOVERY_CACHE: ContextVar[_DiscoveryCache | None] = ContextVar(
    "infralink_command_plugin_catalog", default=None
)


@dataclass(frozen=True)
class ManifestOperation:
    """One import-free operation projection from an installed package manifest."""

    manifest: Mapping[str, Any]
    operation: Mapping[str, Any]

    @property
    def path(self) -> tuple[str, ...]:
        value = self.operation["path"]
        assert isinstance(value, list)
        return tuple(value)

    @property
    def summary(self) -> str:
        value = self.operation["summary"]
        assert isinstance(value, str)
        return value


def _declared_entry_points() -> dict[str, EntryPoint]:
    declared: dict[str, EntryPoint] = {}
    for entry_point in entry_points(group=_GROUP):
        if not entry_point.name:
            continue
        if entry_point.name in declared:
            raise RuntimeError("command_plugin_ambiguous")
        declared[entry_point.name] = entry_point
    return declared


def _manifest_commands() -> dict[str, tuple[Mapping[str, Any], EntryPoint]]:
    """Match each declared command entry point to its built-wheel manifest."""
    cache = _DISCOVERY_CACHE.get()
    if cache is not None and cache.catalog is not None:
        return cache.catalog
    declared = _declared_entry_points()
    commands: dict[str, tuple[Mapping[str, Any], EntryPoint]] = {}
    for name, entry_point in declared.items():
        matches = tuple(
            manifest
            for manifest in installed_manifests()
            if manifest.get("factory") == entry_point.value
        )
        if not matches:
            continue
        if len(matches) != 1:
            raise RuntimeError("command_plugin_manifest_ambiguous")
        manifest = matches[0]
        operations = manifest.get("operations")
        if not isinstance(operations, list) or not operations:
            raise RuntimeError("command_plugin_manifest_invalid")
        if any(
            not isinstance(operation, Mapping)
            or not isinstance(operation.get("path"), list)
            or not operation["path"]
            or operation["path"][0] != name
            for operation in operations
        ):
            raise RuntimeError("command_plugin_manifest_name_invalid")
        commands[name] = (manifest, entry_point)
    if cache is not None:
        cache.catalog = commands
    return commands


@contextmanager
def discovery_scope() -> Iterator[None]:
    """Cache one capability enumeration without retaining installed package state."""
    if _DISCOVERY_CACHE.get() is not None:
        yield
        return
    token = _DISCOVERY_CACHE.set(_DiscoveryCache())
    try:
        yield
    finally:
        _DISCOVERY_CACHE.reset(token)


def names() -> set[str]:
    """Return manifest-backed external command roots without importing plugins."""
    return set(_manifest_commands())


def operations() -> tuple[ManifestOperation, ...]:
    """Return all installed external operations without importing their packages."""
    discovered: list[ManifestOperation] = []
    for manifest, _entry_point in _manifest_commands().values():
        raw_operations = manifest["operations"]
        assert isinstance(raw_operations, list)
        discovered.extend(
            ManifestOperation(manifest=manifest, operation=operation)
            for operation in raw_operations
            if isinstance(operation, Mapping)
        )
    paths = [operation.path for operation in discovered]
    if len(set(paths)) != len(paths):
        raise RuntimeError("command_plugin_path_ambiguous")
    return tuple(sorted(discovered, key=lambda operation: operation.path))


def operation(path: tuple[str, ...]) -> ManifestOperation | None:
    """Return one manifest operation for an exact command path."""
    return next((item for item in operations() if item.path == path), None)


def children(path: tuple[str, ...]) -> tuple[ManifestOperation, ...]:
    """Return immediate manifest children at an external command-group path."""
    depth = len(path)
    return tuple(
        item for item in operations() if len(item.path) == depth + 1 and item.path[:depth] == path
    )


def root_summary(name: str) -> str:
    """Return a concise static root summary from its manifest operations."""
    items = children((name,))
    return items[0].summary if items else ""


def load(name: str) -> click.Command | None:
    """Import one selected app and verify it against its discovery manifest."""
    declared = _manifest_commands().get(name)
    if declared is None:
        return None
    manifest, entry_point = declared
    loaded = entry_point.load()
    app = loaded() if callable(loaded) else loaded
    if not isinstance(app, App):
        raise RuntimeError("command_plugin_invalid")
    try:
        verify_manifest(app, manifest)
    except ManifestMismatch as error:
        raise RuntimeError("command_plugin_manifest_mismatch") from error
    root = mounted_click_command(app)
    command = root.get_command(click.Context(root), name)
    if command is None:
        raise RuntimeError("command_plugin_name_invalid")
    return command
