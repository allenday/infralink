"""Agent-first edits for the local infra-registry working tree."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, MutableMapping
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import click
import yaml
from yaml.nodes import MappingNode, Node, ScalarNode

from infralink.cli.actions import action
from infralink.cli.contracts import (
    Binding,
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


def _managed_runtime_registry_root() -> Path:
    return Path("/var/lib/infralink/registry")


def _registry_root(ctx: Context, *, for_write: bool = False) -> Path:
    root = ctx.registry_path
    if root is None or not root.is_dir():
        raise _failure(
            "Registry authoring requires a directory registry",
            "Provide --registry pointing to the registry hosts directory",
            {"registry": str(root) if root is not None else None},
        )
    resolved = root.resolve()
    runtime_registry = _managed_runtime_registry_root()
    if for_write and (resolved == runtime_registry or runtime_registry in resolved.parents):
        raise _failure(
            "Registry authoring refuses the managed runtime checkout",
            "Run this command in an operator registry working tree, never /var/lib/infralink/registry",
            {"registry": str(root)},
        )
    return resolved


def _manifest_entries(root: Path) -> Iterable[tuple[Path, str, MutableMapping[str, Any]]]:
    for path in sorted(root.glob("**/manifest.yml")):
        try:
            source = path.read_text(encoding="utf-8")
            document = yaml.safe_load(source) or {}
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
        yield path, source, document


def _find_host(
    root: Path, host_ref: str
) -> tuple[str, Path, str, MutableMapping[str, Any], MutableMapping[str, Any]]:
    matches: list[tuple[str, Path, str, MutableMapping[str, Any], MutableMapping[str, Any]]] = []
    for path, source, document in _manifest_entries(root):
        hosts = document.get("hosts")
        if not isinstance(hosts, MutableMapping):
            continue
        for host_id, declaration in hosts.items():
            if not isinstance(host_id, str) or not isinstance(declaration, MutableMapping):
                continue
            canonical_name = declaration.get("canonical_name") or declaration.get("tailscale_name")
            if host_ref == host_id or host_id.startswith(host_ref) or host_ref == canonical_name:
                matches.append((host_id, path, source, document, declaration))
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


def _validate_unique_assignments(assignments: tuple[str, ...]) -> None:
    paths: set[str] = set()
    for assignment in assignments:
        segments, _value = _parse_assignment(assignment)
        path = ".".join(segments)
        if path in paths:
            raise _failure(
                "Mutation paths must be unique",
                "Provide each dot-addressed path at most once",
                {"path": path},
            )
        paths.add(path)


def _public_value(value: Any, *, key: str | None = None) -> Any:
    normalized = (key or "").casefold().replace("-", "_")
    safe_reference = normalized.endswith(("_ref", "_id", "_uuid"))
    secret_shaped = not safe_reference and any(
        marker in normalized for marker in ("password", "token", "secret", "private_key")
    )
    if secret_shaped:
        return "[REDACTED]"
    if isinstance(value, MutableMapping):
        return {
            str(item_key): _public_value(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value


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
    if leaf not in current:
        raise _failure(
            "Mutation path does not exist",
            "Inspect the host declaration and mutate an existing field",
            {"assignment": assignment, "missing_path": ".".join(segments)},
        )
    before = current[leaf]
    if before is not None and type(value) is not type(before):
        raise _failure(
            "Mutation value has the wrong type",
            "Provide YAML with the same type as the existing declaration value",
            {
                "assignment": assignment,
                "path": ".".join(segments),
                "expected_type": type(before).__name__,
                "actual_type": type(value).__name__,
            },
        )
    current[leaf] = value
    return {
        "path": ".".join(segments),
        "before": _public_value(before, key=leaf),
        "after": _public_value(value, key=leaf),
    }


def _mapping_child(node: MappingNode, key: str) -> Node | None:
    for key_node, value_node in node.value:
        if isinstance(key_node, ScalarNode) and key_node.value == key:
            return cast(Node, value_node)
    return None


def _node_reference_counts(root: Node) -> dict[int, int]:
    counts: dict[int, int] = {}

    def visit(node: Node, active: set[int]) -> None:
        node_id = id(node)
        counts[node_id] = counts.get(node_id, 0) + 1
        if node_id in active:
            return
        next_active = {*active, node_id}
        if isinstance(node, MappingNode):
            for key_node, value_node in node.value:
                visit(cast(Node, key_node), next_active)
                visit(cast(Node, value_node), next_active)
        elif hasattr(node, "value") and isinstance(node.value, list):
            for child in node.value:
                visit(cast(Node, child), next_active)

    visit(root, set())
    return counts


def _replace_scalar_assignments(source: str, host_id: str, assignments: tuple[str, ...]) -> str:
    root = yaml.compose(source)
    if not isinstance(root, MappingNode):
        raise _failure(
            "Registry manifest must be a YAML mapping", "Repair the manifest before editing it", {}
        )
    hosts = _mapping_child(root, "hosts")
    if not isinstance(hosts, MappingNode):
        raise _failure(
            "Registry manifest must contain hosts", "Repair the manifest before editing it", {}
        )
    host = _mapping_child(hosts, host_id)
    if not isinstance(host, MappingNode):
        raise _failure(
            "Registry host declaration could not be located",
            "Retry after refreshing the registry",
            {},
        )

    replacements: list[tuple[int, int, str]] = []
    reference_counts = _node_reference_counts(root)
    for assignment in assignments:
        segments, value = _parse_assignment(assignment)
        node: Node = host
        for segment in segments:
            if not isinstance(node, MappingNode):
                raise _failure(
                    "Mutation path does not address a mapping field",
                    "Inspect the host declaration and target an existing scalar field",
                    {"assignment": assignment},
                )
            child = _mapping_child(node, segment)
            if child is None:
                raise _failure(
                    "Mutation path does not exist",
                    "Inspect the host declaration and mutate an existing field",
                    {"assignment": assignment},
                )
            node = child
        if not isinstance(node, ScalarNode) or isinstance(value, (MutableMapping, list)):
            raise _failure(
                "Dot-addressed mutations support existing scalar fields only",
                "Replace a containing mapping through a dedicated typed command",
                {"assignment": assignment},
            )
        if reference_counts[id(node)] != 1:
            raise _failure(
                "Dot-addressed mutations refuse alias-backed fields",
                "Replace the alias with an explicit value manually before using this command",
                {"assignment": assignment},
            )
        replacement = (
            yaml.safe_dump(value, default_flow_style=True, allow_unicode=False)
            .removesuffix("...\n")
            .strip()
        )
        replacements.append((node.start_mark.index, node.end_mark.index, replacement))

    rendered = source
    for start, end, replacement in sorted(replacements, reverse=True):
        rendered = f"{rendered[:start]}{replacement}{rendered[end:]}"
    return rendered


def _validate_candidate(document: MutableMapping[str, Any], host_id: str) -> None:
    from infralink.core.schema import HostSchema

    try:
        hosts = document["hosts"]
        assert isinstance(hosts, MutableMapping)
        HostSchema(**hosts[host_id])
    except (AssertionError, KeyError, TypeError, ValueError) as exc:
        raise _failure(
            "Mutation violates the host declaration schema",
            "Correct the mutation or inspect the host declaration before writing",
            {"host_id": host_id},
        ) from exc


def _write_document(path: Path, rendered: str) -> None:
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
    host_id, manifest_path, _source, _document, declaration = _find_host(root, host_ref)
    result = RegistryHostGetResult(
        host=RegistryHostIdentity(
            id=host_id,
            canonical_name=declaration.get("canonical_name"),
        ),
        manifest_path=str(manifest_path),
        declaration=_public_value(declaration),
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
                    bindings={
                        "path": Binding(type="string", required=True, source="operator.input"),
                        "value": Binding(type="string", required=True, source="operator.input"),
                    },
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
    root = _registry_root(ctx, for_write=write)
    host_id, manifest_path, source, document, declaration = _find_host(root, host_ref)
    _validate_unique_assignments(assignments)
    candidate = deepcopy(document)
    candidate_hosts = candidate.get("hosts")
    assert isinstance(candidate_hosts, MutableMapping)
    candidate_declaration = candidate_hosts[host_id]
    assert isinstance(candidate_declaration, MutableMapping)
    changes = [_apply_assignment(candidate_declaration, assignment) for assignment in assignments]
    _validate_candidate(candidate, host_id)
    if write:
        _write_document(manifest_path, _replace_scalar_assignments(source, host_id, assignments))
    result = RegistryHostPatchResult(
        mode="written" if write else "preview",
        host=RegistryHostIdentity(
            id=host_id,
            canonical_name=candidate_declaration.get("canonical_name"),
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
