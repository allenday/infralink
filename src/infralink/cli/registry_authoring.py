"""Agent-first edits for the local infra-registry working tree."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, MutableMapping
from pathlib import Path
from typing import Any

import click
import yaml

from infralink.cli.actions import action
from infralink.cli.contracts import (
    RegistryHostGetResult,
    RegistryHostIdentity,
    RegistryHostPatchResult,
    RegistryMutation,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.output import ok_envelope

try:
    from infralink.cli.main import Context, _context_for, _emit, _root_source_argv, pass_context
except ModuleNotFoundError:
    Context = object  # type: ignore[misc,assignment]

    def pass_context(func: Any) -> Any:
        return func


def _failure(message: str, fix: str, details: dict[str, Any]) -> CliFailure:
    return CliFailure(
        code=ErrorCode.USAGE_ERROR,
        message=message,
        exit_code=ExitCode.USAGE_ERROR,
        fix=fix,
        details=details,
    )


def _registry_root(ctx: Context) -> Path:
    root = ctx.registry_path
    if root is None or not root.is_dir():
        raise _failure(
            "Registry authoring requires a directory registry",
            "Provide --registry pointing to the registry hosts directory",
            {"registry": str(root) if root is not None else None},
        )
    return root


def _manifest_entries(root: Path) -> Iterable[tuple[Path, MutableMapping[str, Any]]]:
    for path in sorted(root.glob("**/manifest.yml")):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise _failure(
                "Registry manifest is not valid YAML",
                "Repair the manifest before editing it",
                {"manifest_path": str(path)},
            ) from exc
        if not isinstance(document, MutableMapping):
            raise _failure(
                "Registry manifest must be a YAML mapping",
                "Repair the manifest before editing it",
                {"manifest_path": str(path)},
            )
        yield path, document


def _find_host(
    root: Path, host_ref: str
) -> tuple[str, Path, MutableMapping[str, Any], MutableMapping[str, Any]]:
    matches: list[tuple[str, Path, MutableMapping[str, Any], MutableMapping[str, Any]]] = []
    for path, document in _manifest_entries(root):
        hosts = document.get("hosts")
        if not isinstance(hosts, MutableMapping):
            continue
        for host_id, declaration in hosts.items():
            if not isinstance(host_id, str) or not isinstance(declaration, MutableMapping):
                continue
            canonical_name = declaration.get("canonical_name") or declaration.get("tailscale_name")
            if host_ref == host_id or host_id.startswith(host_ref) or host_ref == canonical_name:
                matches.append((host_id, path, document, declaration))
    if not matches:
        raise _failure(
            "Registry host was not found",
            "Use infralink registry host get with a declared UUID or canonical name",
            {"host_ref": host_ref},
        )
    if len(matches) > 1:
        raise _failure(
            "Registry host reference is ambiguous",
            "Use the complete host UUID",
            {"host_ref": host_ref, "matches": [item[0] for item in matches]},
        )
    return matches[0]


def _parse_assignment(value: str) -> tuple[list[str], Any]:
    path, separator, encoded_value = value.partition("=")
    if not separator or not path or any(not segment for segment in path.split(".")):
        raise _failure(
            "Mutation must use PATH=YAML_VALUE",
            "Use a dot-addressed path, for example controller_bootstrap.controller_image=ghcr.io/example/controller:v0.5.5",
            {"assignment": value},
        )
    try:
        decoded_value = yaml.safe_load(encoded_value)
    except yaml.YAMLError as exc:
        raise _failure(
            "Mutation value is not valid YAML",
            "Quote the value or provide a valid YAML scalar, list, or mapping",
            {"assignment": value},
        ) from exc
    return path.split("."), decoded_value


def _apply_assignment(declaration: MutableMapping[str, Any], assignment: str) -> dict[str, Any]:
    segments, value = _parse_assignment(assignment)
    current: MutableMapping[str, Any] = declaration
    for segment in segments[:-1]:
        child = current.get(segment)
        if not isinstance(child, MutableMapping):
            raise _failure(
                "Mutation parent path does not exist",
                "Inspect the host declaration and target an existing mapping path",
                {"assignment": assignment, "missing_parent": ".".join(segments[:-1])},
            )
        current = child
    leaf = segments[-1]
    before = current.get(leaf)
    current[leaf] = value
    return {"path": ".".join(segments), "before": before, "after": value}


def _write_document(path: Path, document: MutableMapping[str, Any]) -> None:
    rendered = yaml.safe_dump(dict(document), sort_keys=False, allow_unicode=False)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as output:
            output.write(rendered)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise _failure(
            "Registry manifest could not be written",
            "Check working-tree permissions and retry",
            {"manifest_path": str(path)},
        ) from exc


@click.group(name="registry")
def registry() -> None:
    """Inspect and author local registry declarations."""


@registry.group(name="host")
def registry_host() -> None:
    """Inspect and patch host declarations in the local registry working tree."""


@registry_host.command(name="get")
@click.argument("host_ref")
@pass_context
def registry_host_get(ctx: Context, host_ref: str) -> None:
    """Resolve a host to its authoritative manifest and declaration."""
    root = _registry_root(ctx)
    host_id, manifest_path, _document, declaration = _find_host(root, host_ref)
    result = RegistryHostGetResult(
        host=RegistryHostIdentity(
            id=host_id,
            canonical_name=declaration.get("canonical_name"),
        ),
        manifest_path=str(manifest_path),
        declaration=dict(declaration),
    )
    _emit(
        ok_envelope(
            _context_for(path=["registry", "host", "get"]),
            result,
            [
                action(
                    "patch",
                    [
                        *_root_source_argv(ctx),
                        "registry",
                        "host",
                        "patch",
                        host_id,
                        "--set",
                        "{path}={value}",
                    ],
                    "Preview a typed host declaration mutation",
                )
            ],
        )
    )


@registry_host.command(name="patch")
@click.argument("host_ref")
@click.option("--set", "assignments", multiple=True, required=True, metavar="PATH=YAML_VALUE")
@click.option(
    "--write", is_flag=True, help="Atomically write the validated mutation to the manifest"
)
@pass_context
def registry_host_patch(
    ctx: Context,
    host_ref: str,
    assignments: tuple[str, ...],
    write: bool,
) -> None:
    """Preview or explicitly write typed dot-addressed host mutations."""
    root = _registry_root(ctx)
    host_id, manifest_path, document, declaration = _find_host(root, host_ref)
    changes = [_apply_assignment(declaration, assignment) for assignment in assignments]
    if write:
        _write_document(manifest_path, document)
    result = RegistryHostPatchResult(
        mode="written" if write else "preview",
        host=RegistryHostIdentity(
            id=host_id,
            canonical_name=declaration.get("canonical_name"),
        ),
        manifest_path=str(manifest_path),
        changes=[RegistryMutation(**change) for change in changes],
    )
    actions = [
        action(
            "get",
            [*_root_source_argv(ctx), "registry", "host", "get", host_id],
            "Inspect the host declaration",
        )
    ]
    if not write:
        actions.append(
            action(
                "write",
                [
                    *_root_source_argv(ctx),
                    "registry",
                    "host",
                    "patch",
                    host_id,
                    *[item for assignment in assignments for item in ("--set", assignment)],
                    "--write",
                ],
                "Write this reviewed mutation",
                safe=False,
            )
        )
    _emit(ok_envelope(_context_for(path=["registry", "host", "patch"]), result, actions))
