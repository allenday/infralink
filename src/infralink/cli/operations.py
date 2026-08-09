"""Woodpecker-backed durable host apply operations."""

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

_OPERATION_ID = re.compile(r"^woodpecker/(?P<repository>[1-9][0-9]*)/(?P<number>[1-9][0-9]*)$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_CHANNEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WOODPECKER_URL = "INFRALINK_WOODPECKER_URL"
_WOODPECKER_REPOSITORY_ID = "INFRALINK_WOODPECKER_REPOSITORY_ID"
_WOODPECKER_BRANCH = "INFRALINK_WOODPECKER_BRANCH"
_WOODPECKER_TOKEN_ENV = "INFRALINK_WOODPECKER_TOKEN_ENV"


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

class OperationProvider(Protocol):
    def submit(self, request: ApplyRequest) -> OperationRecord: ...

    def status(self, operation_id: str) -> OperationRecord: ...


class WoodpeckerOperationProvider:
    """Use a Woodpecker pipeline as the durable apply-operation record."""

    def __init__(self, *, url: str, repository_id: int, branch: str, token: str) -> None:
        self._url = url.rstrip("/")
        self._repository_id = repository_id
        self._branch = branch
        self._token = token

    def submit(self, request: ApplyRequest) -> OperationRecord:
        payload = {
            "branch": self._branch,
            "variables": {
                "INFRALINK_APPLY_HOST_UUID": request.host_uuid,
                "INFRALINK_APPLY_REGISTRY_REVISION": request.registry_revision,
                "INFRALINK_APPLY_SELECTOR": request.selector,
            },
        }
        pipeline = self._request("POST", "/pipelines", payload)
        return self._record_from_pipeline(pipeline)

    def status(self, operation_id: str) -> OperationRecord:
        repository_id, number = self._parse_operation_id(operation_id)
        if repository_id != self._repository_id:
            raise CliFailure(
                code=ErrorCode.USAGE_ERROR,
                message="Operation belongs to a different Woodpecker repository",
                exit_code=ExitCode.USAGE_ERROR,
                fix="Use an operation ID returned by this control host",
                details={"operation_id": operation_id},
            )
        pipeline = self._request("GET", f"/pipelines/{number}")
        return self._record_from_pipeline(pipeline, expected_number=number)

    def _request(self, method: str, path: str, body: dict[str, object] | None = None) -> object:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(
            f"{self._url}/api/repos/{self._repository_id}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._token}",
            },
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - configured control host
                return json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError, ValueError):
            raise _provider_failure("Woodpecker operation is unavailable") from None

    def _record_from_pipeline(self, value: object, *, expected_number: int | None = None) -> OperationRecord:
        if not isinstance(value, dict):
            raise _provider_failure("Woodpecker returned an invalid pipeline record")
        number = value.get("number")
        status = value.get("status")
        if not isinstance(number, int) or number < 1 or (expected_number is not None and number != expected_number):
            raise _provider_failure("Woodpecker returned an invalid pipeline number")
        if not isinstance(status, str):
            raise _provider_failure("Woodpecker returned an invalid pipeline status")
        state = {
            "pending": "queued",
            "running": "applying",
            "blocked": "applying",
            "success": "converged",
            "failure": "failed",
            "error": "failed",
            "killed": "failed",
        }.get(status)
        if state is None:
            raise _provider_failure("Woodpecker returned an unsupported pipeline status")
        return OperationRecord(id=f"woodpecker/{self._repository_id}/{number}", state=state)

    @staticmethod
    def _parse_operation_id(operation_id: str) -> tuple[int, int]:
        match = _OPERATION_ID.fullmatch(operation_id)
        if match is None:
            raise CliFailure(
                code=ErrorCode.USAGE_ERROR,
                message="Operation ID is invalid",
                exit_code=ExitCode.USAGE_ERROR,
                fix="Use the opaque operation ID returned by host apply",
                details={"operation_id": operation_id},
            )
        return int(match.group("repository")), int(match.group("number"))


def operation_provider_from_environment() -> OperationProvider:
    values = {
        _WOODPECKER_URL: os.environ.get(_WOODPECKER_URL),
        _WOODPECKER_REPOSITORY_ID: os.environ.get(_WOODPECKER_REPOSITORY_ID),
        _WOODPECKER_BRANCH: os.environ.get(_WOODPECKER_BRANCH),
        _WOODPECKER_TOKEN_ENV: os.environ.get(_WOODPECKER_TOKEN_ENV),
    }
    missing = sorted(name for name, value in values.items() if not value)
    if missing:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host apply Woodpecker control host is not configured",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Configure the required INFRALINK_WOODPECKER_* control-host environment",
            details={"missing_environment": missing},
        )
    repository_id_text = values[_WOODPECKER_REPOSITORY_ID]
    token_environment = values[_WOODPECKER_TOKEN_ENV]
    assert repository_id_text is not None
    assert token_environment is not None
    if not repository_id_text.isdigit() or int(repository_id_text) < 1:
        raise _configuration_failure(_WOODPECKER_REPOSITORY_ID)
    if _ENVIRONMENT_NAME.fullmatch(token_environment) is None:
        raise _configuration_failure(_WOODPECKER_TOKEN_ENV)
    token = os.environ.get(token_environment)
    if not token:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host apply Woodpecker token is not configured",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix=f"Set the environment named by {_WOODPECKER_TOKEN_ENV}",
            details={"token_environment": token_environment},
        )
    url = values[_WOODPECKER_URL]
    branch = values[_WOODPECKER_BRANCH]
    assert url is not None
    assert branch is not None
    return WoodpeckerOperationProvider(
        url=url,
        repository_id=int(repository_id_text),
        branch=branch,
        token=token,
    )


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


def _configuration_failure(environment: str) -> CliFailure:
    return CliFailure(
        code=ErrorCode.CONFIGURATION_REQUIRED,
        message="Host apply Woodpecker control host configuration is invalid",
        exit_code=ExitCode.PROVIDER_ERROR,
        fix="Correct the configured INFRALINK_WOODPECKER_* control-host environment",
        details={"environment": environment},
    )


def _provider_failure(message: str) -> CliFailure:
    return CliFailure(
        code=ErrorCode.PROVIDER_UNAVAILABLE,
        message=message,
        exit_code=ExitCode.PROVIDER_ERROR,
        fix="Retry the operation or inspect the control plane",
    )
