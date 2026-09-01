"""Typed host-authoring operations for a Registry working tree."""

from __future__ import annotations

import re
from ipaddress import ip_address
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import yaml
from agent_surface import OperationError

from infralink.core.schema import HostSchema
from infralink.operator_sources import load_registry, managed_runtime_registry_root

if TYPE_CHECKING:
    from infralink.operator_surface import HostCreateRequest

_HOSTNAME_PATTERN = re.compile(
    r"(?=.{1,253}\Z)(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)*"
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\Z"
)


def host_address(value: str) -> tuple[str, str]:
    """Classify one declarative Tailnet address without accepting malformed input."""
    try:
        return "tailscale_ip", str(ip_address(value))
    except ValueError:
        if _HOSTNAME_PATTERN.fullmatch(value):
            return "tailscale_name", value.lower()
    raise OperationError(
        "usage_error",
        "Host address must be an IP address or DNS hostname",
        details=({"address": value},),
        fix="Pass a valid --address value.",
    )


def create_host(request: HostCreateRequest) -> dict[str, Any]:
    """Render or explicitly write one host manifest from the selected checkout."""
    address_field, normalized_address = host_address(request.address)
    host_id = str(uuid4())
    host: dict[str, Any] = {
        "canonical_name": request.name,
        "status": "provisioning",
        address_field: normalized_address,
    }
    HostSchema(**host)
    manifest = {"hosts": {host_id: host}}
    result: dict[str, Any] = {
        "mode": "dry_run",
        "host_id": host_id,
        "address": {
            "field": address_field,
            "value": normalized_address,
            "reason": (
                "input is an IP address"
                if address_field == "tailscale_ip"
                else "input is a DNS hostname and maps to tailscale_name"
            ),
        },
        "manifest_path": None,
        "manifest": manifest,
    }
    if not request.write:
        return result

    if request.registry is None:
        raise OperationError(
            "configuration_required",
            "Host create --write requires a registry checkout root",
            details=({"source": "registry"},),
            fix="Pass --registry pointing to a writable Registry checkout root.",
        )
    sources = load_registry(request)
    checkout = sources.registry_path
    runtime = managed_runtime_registry_root().resolve()
    if checkout == runtime or runtime in checkout.parents:
        raise OperationError(
            "authoring_checkout_required",
            "Host create --write requires an operator registry working tree",
            details=({"registry": str(checkout)},),
            fix=(
                "Use a writable authoring checkout, commit the generated manifest, "
                "and let normal self-deploy fetch the merged registry revision."
            ),
        )
    if sources.registry.get_by_name(request.name) is not None:
        raise OperationError(
            "usage_error",
            "Host canonical name already exists",
            details=({"name": request.name},),
            fix="Choose a unique --name or update the existing host manifest.",
        )

    host_root = checkout / "hosts"
    manifest_path = host_root / host_id / "manifest.yml"
    _require_authoring_destination(checkout, runtime, host_root)
    _require_authoring_destination(checkout, runtime, manifest_path.parent)
    if manifest_path.parent.exists():
        raise OperationError(
            "usage_error",
            "Generated host UUID already exists",
            details=({"host_id": host_id},),
            fix="Run host create again to generate a new host UUID.",
        )
    manifest_path.parent.mkdir(mode=0o755)
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    result.update(
        {
            "mode": "written",
            "manifest_path": manifest_path,
            "write_state": "local_uncommitted",
            "git_worktree": checkout,
        }
    )
    return result


def _require_authoring_destination(checkout: Path, runtime: Path, destination: Path) -> None:
    """Reject symlink traversal before a host authoring operation writes anything."""
    resolved = destination.resolve()
    if not _within(checkout, resolved) or _within(runtime, resolved):
        raise OperationError(
            "authoring_checkout_required",
            "Host authoring destination must not resolve inside the managed runtime",
            details=({"registry": str(checkout), "destination": str(destination)},),
            fix="Use an ordinary Registry working tree whose hosts directory is not a runtime symlink.",
        )


def _within(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents
