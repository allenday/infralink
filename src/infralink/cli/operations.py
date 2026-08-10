"""Declared SSH transport for host-local reconcile operations."""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import yaml

from infralink.cli.errors import CliFailure, ErrorCode, ExitCode

_OPERATION_ID = re.compile(
    r"^ssh/(?P<host>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"(?P<invocation>[0-9a-f]{32})$"
)
_LEGACY_OPERATION_ID = re.compile(r"^op_[A-Za-z0-9_-]{8,128}$")
_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_UNIT = "self-deploy-v2-reconcile.service"

_START_REMOTE = """set -eu
unit=$1
systemctl start --no-block "$unit"
systemctl show "$unit" -p InvocationID -p ActiveState -p Result -p ExecMainStatus
"""
_STATUS_REMOTE = """set -eu
unit=$1
invocation=$2
systemctl show "$unit" -p InvocationID -p ActiveState -p Result -p ExecMainStatus
journalctl --quiet --no-pager --output=cat _SYSTEMD_INVOCATION_ID="$invocation" || true
"""


@dataclass(frozen=True)
class ApplyRequest:
    """One host declaration resolved to its sole permitted remote action."""

    host_uuid: str
    canonical_name: str
    address: str
    port: int
    user: str
    host_key_fingerprint: str
    unit: str


@dataclass(frozen=True)
class OperationRecord:
    id: str
    state: str
    target: dict[str, str] | None = None


class OperationProvider(Protocol):
    def submit(self, request: ApplyRequest) -> OperationRecord: ...

    def status(self, operation_id: str, request: ApplyRequest) -> OperationRecord: ...


class SshOperationProvider:
    """Start and inspect one declared systemd reconcile unit over SSH."""

    def submit(self, request: ApplyRequest) -> OperationRecord:
        values = self._run(request, _START_REMOTE)
        return self._record(request, values)

    def status(self, operation_id: str, request: ApplyRequest) -> OperationRecord:
        match = _OPERATION_ID.fullmatch(operation_id)
        if match is None or match.group("host") != request.host_uuid:
            raise _usage_failure("Operation ID does not belong to the declared host", operation_id)
        values = self._run(request, _STATUS_REMOTE, match.group("invocation"))
        invocation = values.get("InvocationID", "").lower()
        if invocation == match.group("invocation"):
            return self._record(request, values)
        state = _journal_state(values.get("journal", []))
        if state is None:
            raise _provider_failure("Declared host has not recorded the requested reconcile run")
        return OperationRecord(
            id=operation_id,
            state=state,
            target={
                "type": "host",
                "id": request.host_uuid,
                "canonical_name": request.canonical_name,
            },
        )

    def _run(
        self, request: ApplyRequest, script: str, invocation: str | None = None
    ) -> dict[str, Any]:
        remote_args = [request.unit] if invocation is None else [request.unit, invocation]
        try:
            with _pinned_known_hosts(request) as known_hosts:
                completed = subprocess.run(
                    [
                        "ssh",
                        "-o",
                        "BatchMode=yes",
                        "-o",
                        "ConnectTimeout=10",
                        "-o",
                        "LogLevel=ERROR",
                        "-o",
                        "StrictHostKeyChecking=yes",
                        "-o",
                        f"UserKnownHostsFile={known_hosts}",
                        "-p",
                        str(request.port),
                        f"{request.user}@{request.address}",
                        "sh",
                        "-s",
                        "--",
                        *remote_args,
                    ],
                    input=script,
                    text=True,
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
        except (OSError, subprocess.TimeoutExpired):
            raise _provider_failure(
                "Declared host SSH reconcile operation is unavailable"
            ) from None
        if completed.returncode != 0:
            raise _provider_failure("Declared host rejected the reconcile operation")
        values = _parse_properties(completed.stdout)
        if not values.get("InvocationID"):
            raise _provider_failure("Declared host returned no reconcile run reference")
        return values

    @staticmethod
    def _record(request: ApplyRequest, values: dict[str, Any]) -> OperationRecord:
        invocation = values["InvocationID"].lower()
        if not _is_uuid(invocation):
            raise _provider_failure("Declared host returned an invalid reconcile run reference")
        state = _state(values)
        return OperationRecord(
            id=f"ssh/{request.host_uuid}/{invocation}",
            state=state,
            target={
                "type": "host",
                "id": request.host_uuid,
                "canonical_name": request.canonical_name,
            },
        )


def operation_provider() -> OperationProvider:
    """Return the sole supported host-apply transport."""
    return SshOperationProvider()


def resolve_apply_request(registry_path: Path, host: Any) -> ApplyRequest:
    """Resolve and validate the host's explicit SSH reconcile declaration."""
    if not registry_path.is_dir():
        raise _registry_failure("Host apply requires a directory registry checkout", registry_path)
    contract_path = registry_path / host.uuid / "operations" / "contract.yml"
    contract = _read_mapping(contract_path, "Host apply requires a declared SSH reconcile contract")
    machine = contract.get("machine")
    transport = contract.get("transport")
    reconcile = contract.get("reconcile")
    if not isinstance(machine, dict) or machine.get("uuid") != host.uuid:
        raise _registry_failure("Host apply contract does not match the target host", contract_path)
    if machine.get("canonical_name") != host.canonical_name:
        raise _registry_failure(
            "Host apply contract canonical name does not match the target host", contract_path
        )
    if not isinstance(transport, dict) or transport.get("kind") != "ssh":
        raise _registry_failure("Host apply contract must declare SSH transport", contract_path)
    if not isinstance(reconcile, dict) or reconcile.get("unit") != _UNIT:
        raise _registry_failure(
            "Host apply contract must declare the supported reconcile unit", contract_path
        )
    address = transport.get("host")
    port = transport.get("port")
    user = transport.get("user")
    fingerprint = transport.get("host_key_fingerprint")
    if (
        not isinstance(address, str)
        or not address
        or any(character.isspace() for character in address)
    ):
        raise _registry_failure("Host apply contract SSH address is invalid", contract_path)
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise _registry_failure("Host apply contract SSH port is invalid", contract_path)
    if user != "root":
        raise _registry_failure("Host apply contract must declare root SSH user", contract_path)
    if not isinstance(fingerprint, str) or _FINGERPRINT.fullmatch(fingerprint) is None:
        raise _registry_failure(
            "Host apply contract SSH host key fingerprint is invalid", contract_path
        )
    return ApplyRequest(host.uuid, host.canonical_name, address, port, user, fingerprint, _UNIT)


def wait_for_terminal(
    provider: OperationProvider, operation_id: str, request: ApplyRequest, *, timeout_seconds: int
) -> OperationRecord:
    """Poll the remote unit's own machine-readable status."""
    deadline = time.monotonic() + timeout_seconds
    record = provider.status(operation_id, request)
    while record.state == "applying" and time.monotonic() < deadline:
        time.sleep(1)
        record = provider.status(operation_id, request)
    return record


def _parse_properties(stdout: str) -> dict[str, Any]:
    values: dict[str, Any] = {"journal": []}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in {"InvocationID", "ActiveState", "Result", "ExecMainStatus"}:
            values[key] = value
            continue
        try:
            document = yaml.safe_load(line)
        except yaml.YAMLError:
            continue
        if isinstance(document, dict):
            values["journal"].append(document)
    return values


def _state(values: dict[str, Any]) -> str:
    if values.get("ActiveState") in {"active", "activating", "reloading", "deactivating"}:
        return "applying"
    if values.get("Result") == "success" and values.get("ExecMainStatus") == "0":
        return "converged"
    return "failed"


def _is_uuid(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{32}", value))


def _journal_state(records: object) -> str | None:
    if not isinstance(records, list):
        return None
    for record in reversed(records):
        if isinstance(record, dict) and type(record.get("ok")) is bool:
            return "converged" if record["ok"] else "failed"
    return None


@contextmanager
def _pinned_known_hosts(request: ApplyRequest) -> Iterator[Path]:
    """Use only a scanned key whose declared SHA256 fingerprint matches."""
    try:
        scanned = subprocess.run(
            ["ssh-keyscan", "-T", "10", "-p", str(request.port), request.address],
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise _provider_failure("Declared host SSH key is unavailable") from None
    if scanned.returncode != 0 or not scanned.stdout:
        raise _provider_failure("Declared host SSH key is unavailable")
    matching = []
    for line in scanned.stdout.splitlines():
        if not line or line.startswith("#"):
            continue
        try:
            fingerprint = subprocess.run(
                ["ssh-keygen", "-lf", "-", "-E", "sha256"],
                input=f"{line}\n",
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if fingerprint.returncode == 0 and request.host_key_fingerprint == _ssh_fingerprint(
            fingerprint.stdout
        ):
            matching.append(line)
    if not matching:
        raise _provider_failure("Declared host SSH key does not match its fingerprint")
    with tempfile.TemporaryDirectory(prefix="infralink-known-hosts-") as directory:
        path = Path(directory) / "known_hosts"
        path.write_text("\n".join(matching) + "\n", encoding="utf-8")
        path.chmod(0o600)
        yield path


def _ssh_fingerprint(stdout: str) -> str | None:
    for value in stdout.split():
        if _FINGERPRINT.fullmatch(value):
            return value
    return None


def _read_mapping(path: Path, message: str) -> dict[str, Any]:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        raise _registry_failure(message, path) from None
    if not isinstance(value, dict):
        raise _registry_failure(message, path)
    return value


def _registry_failure(message: str, path: Path) -> CliFailure:
    return CliFailure(
        code=ErrorCode.INPUT_LOAD_FAILED,
        message=message,
        exit_code=ExitCode.INPUT_ERROR,
        fix="Declare a matching SSH reconcile contract and retry",
        details={"path": str(path)},
    )


def _usage_failure(message: str, operation_id: str) -> CliFailure:
    return CliFailure(
        code=ErrorCode.USAGE_ERROR,
        message=message,
        exit_code=ExitCode.USAGE_ERROR,
        fix="Use the opaque run reference returned by host apply for this host",
        details={"operation_id": operation_id},
    )


def _provider_failure(message: str) -> CliFailure:
    return CliFailure(
        code=ErrorCode.PROVIDER_UNAVAILABLE,
        message=message,
        exit_code=ExitCode.PROVIDER_ERROR,
        fix="Retry the host apply or inspect the host-local reconcile unit",
    )
