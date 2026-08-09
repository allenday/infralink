"""Provider-neutral control-plane operation client for host apply."""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import yaml

from infralink.cli.errors import CliFailure, ErrorCode, ExitCode

_OPERATION_ID = re.compile(r"^op_[A-Za-z0-9_-]{8,128}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_CHANNEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_CONTROL_PLANE_URL = "INFRALINK_CONTROL_PLANE_URL"


@dataclass(frozen=True)
class ApplyRequest:
    """The only provider input exposed by host apply."""

    host_uuid: str
    registry_revision: str
    selector: str

    def as_payload(self) -> dict[str, str]:
        return {
            "host_uuid": self.host_uuid,
            "registry_revision": self.registry_revision,
            "selector": self.selector,
        }


@dataclass(frozen=True)
class OperationRecord:
    id: str
    state: str
    target: dict[str, str] | None = None

    @classmethod
    def from_payload(cls, value: object) -> OperationRecord:
        if not isinstance(value, dict):
            raise _provider_failure("Control plane returned an invalid operation record")
        operation_id = value.get("id")
        state = value.get("state")
        if not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id):
            raise _provider_failure("Control plane returned an invalid operation ID")
        if state not in {"queued", "applying", "converged", "failed"}:
            raise _provider_failure("Control plane returned an invalid operation state")
        target = value.get("target")
        if target is not None and (
            not isinstance(target, dict)
            or target.get("type") != "host"
            or not isinstance(target.get("id"), str)
            or not isinstance(target.get("canonical_name"), str)
        ):
            raise _provider_failure("Control plane returned an invalid operation target")
        return cls(id=operation_id, state=state, target=target)


class OperationProvider(Protocol):
    def submit(self, request: ApplyRequest) -> OperationRecord: ...

    def status(self, operation_id: str) -> OperationRecord: ...


class HttpOperationProvider:
    """Minimal HTTP adapter; provider implementation owns operation durability."""

    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")

    def submit(self, request: ApplyRequest) -> OperationRecord:
        return OperationRecord.from_payload(self._request("POST", "/operations", request.as_payload()))

    def status(self, operation_id: str) -> OperationRecord:
        if not _OPERATION_ID.fullmatch(operation_id):
            raise CliFailure(
                code=ErrorCode.USAGE_ERROR,
                message="Operation ID is invalid",
                exit_code=ExitCode.USAGE_ERROR,
                fix="Use the opaque operation ID returned by host apply",
                details={"operation_id": operation_id},
            )
        return OperationRecord.from_payload(self._request("GET", f"/operations/{operation_id}"))

    def _request(self, method: str, path: str, body: dict[str, str] | None = None) -> object:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{self._base_url}{path}",
            data=data,
            method=method,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - configured control plane
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError):
            raise _provider_failure("Control plane operation is unavailable") from None


def operation_provider_from_environment() -> OperationProvider:
    url = os.environ.get(_CONTROL_PLANE_URL)
    if not url:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host apply control plane is not configured",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix=f"Set {_CONTROL_PLANE_URL} for the configured control-plane adapter",
            details={"environment": _CONTROL_PLANE_URL},
        )
    return HttpOperationProvider(url)


def resolve_apply_request(registry_path: Path, host: Any) -> ApplyRequest:
    """Resolve one declared host to a target-set pointer at immutable HEAD."""
    if not registry_path.is_dir():
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host apply requires a directory registry checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Provide --registry pointing to the checked-out hosts directory",
            details={"registry": str(registry_path)},
        )
    root = _git_toplevel(registry_path)
    revision = _git_revision(root)
    policy_path = registry_path / host.uuid / "operations" / "release-policy.yml"
    policy = _read_mapping(policy_path, "Host apply policy is missing or invalid")
    channel = _channel_from_policy(policy, host.uuid, policy_path)
    selector = f"release-channels/{channel}.yml"
    target_set = _read_mapping(root / selector, "Host apply target set is missing or invalid")
    _validate_target_set(target_set, host.uuid, policy_path.relative_to(root).as_posix(), selector)
    return ApplyRequest(host_uuid=host.uuid, registry_revision=revision, selector=selector)


def wait_for_terminal(
    provider: OperationProvider, operation_id: str, *, timeout_seconds: int
) -> OperationRecord:
    """Poll a durable provider operation without storing client-side state."""
    deadline = time.monotonic() + timeout_seconds
    record = provider.status(operation_id)
    while record.state in {"queued", "applying"} and time.monotonic() < deadline:
        time.sleep(1)
        record = provider.status(operation_id)
    return record


def _git_toplevel(path: Path) -> Path:
    try:
        output = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise _registry_failure("Host apply requires a Git registry checkout", path) from None
    return Path(output)


def _git_revision(root: Path) -> str:
    try:
        revision = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        raise _registry_failure("Host apply could not resolve the registry revision", root) from None
    if not _REVISION.fullmatch(revision):
        raise _registry_failure("Host apply resolved an invalid registry revision", root)
    return revision


def _read_mapping(path: Path, message: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise _registry_failure(message, path) from None
    if not isinstance(value, dict):
        raise _registry_failure(message, path)
    return value


def _channel_from_policy(policy: dict[str, Any], host_uuid: str, path: Path) -> str:
    release = policy.get("release")
    if policy.get("host_uuid") != host_uuid or not isinstance(release, dict):
        raise _registry_failure("Host apply policy does not match the target host", path)
    channel = release.get("channel")
    if not isinstance(channel, str) or not _CHANNEL.fullmatch(channel):
        raise _registry_failure("Host apply policy has an invalid target channel", path)
    return channel


def _validate_target_set(
    target_set: dict[str, Any], host_uuid: str, policy_path: str, selector: str
) -> None:
    if target_set.get("schema_version") != "infralink.release-target-set.v1":
        raise _registry_failure("Host apply target set has an unsupported schema", Path(selector))
    targets = target_set.get("targets")
    if not isinstance(targets, list) or not any(
        isinstance(item, dict)
        and item.get("host_uuid") == host_uuid
        and item.get("policy") == policy_path
        for item in targets
    ):
        raise _registry_failure("Host is not selected by its declared target set", Path(selector))


def _registry_failure(message: str, path: Path) -> CliFailure:
    return CliFailure(
        code=ErrorCode.INPUT_LOAD_FAILED,
        message=message,
        exit_code=ExitCode.INPUT_ERROR,
        fix="Correct the declared host apply configuration and retry",
        details={"path": str(path)},
    )


def _provider_failure(message: str) -> CliFailure:
    return CliFailure(
        code=ErrorCode.PROVIDER_UNAVAILABLE,
        message=message,
        exit_code=ExitCode.PROVIDER_ERROR,
        fix="Retry the operation or inspect the control plane",
    )
