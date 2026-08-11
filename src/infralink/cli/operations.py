"""Declared SSH transport for host-local reconcile operations."""

from __future__ import annotations

import ipaddress
import re
import subprocess
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit

import yaml

from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.operation_contracts import (
    AllowedSignerDiagnostic,
    HostVerifierDiagnostic,
    OperationFailure,
    OperationUnitFailure,
    VerifierUnavailableFact,
)

_OPERATION_ID = re.compile(
    r"^ssh/(?P<host>[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})/"
    r"(?P<invocation>[0-9a-f]{32})$"
)
_LEGACY_OPERATION_ID = re.compile(r"^op_[A-Za-z0-9_-]{8,128}$")
_FINGERPRINT = re.compile(r"^SHA256:[A-Za-z0-9+/]{43}$")
_CHANNEL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
_UNIT = "self-deploy-v2-reconcile.service"
_JOURNAL_SEPARATOR = "__INFRALINK_JOURNAL__"
_DIAGNOSTIC_CODE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
_DIAGNOSTIC_STAGES = frozenset({"inspect", "validate", "plan", "apply", "verify", "record"})
_MAX_FAILURE_JOURNAL_LINES = 6
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_SIGNER_PRINCIPAL = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_MANIFEST_V2_APPLY_FIELDS = frozenset(
    {
        "self_deploy_v2_reconcile_enabled",
        "self_deploy_v2_reconcile_packaged",
        "self_deploy_v2_promotion_policy_enabled",
        "self_deploy_v2_promotion_channel",
        "self_deploy_v2_promotion_host_fingerprint",
    }
)

_START_REMOTE = """set -eu
unit=$1
systemctl start --no-block "$unit"
systemctl show "$unit" -p InvocationID -p ActiveState -p Result -p ExecMainStatus
"""
_STATUS_REMOTE = """set -eu
unit=$1
invocation=$2
systemctl show "$unit" -p InvocationID -p ActiveState -p Result -p ExecMainStatus
printf '%s\n' '__INFRALINK_JOURNAL__'
journalctl --quiet --no-pager --output=cat _SYSTEMD_INVOCATION_ID="$invocation" || true
"""
_VERIFIER_REMOTE = """set -eu
bootstrap=/etc/infra-management/self-deploy-bootstrap.yml
signers=/etc/infra-management/registry-allowed-signers
registry=/var/lib/infralink/registry

value() {
    sed -n "s/^    $1: //p" "$bootstrap" | head -n 1 | sed 's/^"//; s/"$//'
}
remote=$(value remote)
ref=$(value ref)
runtime=$(systemctl show self-deploy-v2-reconcile.service -p Environment --value | tr ' ' '\\n' | sed -n 's#^SELF_DEPLOY_V2_RUNTIME_ROOT=/var/lib/self-deploy-v2/runtime/##p' | head -n 1)
principal=$(awk 'NF >= 3 { print $1; exit }' "$signers")
fingerprint=$(awk 'NF >= 3 { print $2 " " $3; exit }' "$signers" | ssh-keygen -lf - -E sha256 2>/dev/null | awk '{ print $2; exit }')
digest=$(sha256sum "$signers" | awk '{ print $1 }')
tip=$(GIT_CONFIG_NOSYSTEM=1 git -C "$registry" rev-parse --verify refs/remotes/origin/main^{commit} 2>/dev/null || true)
version=$(git --version | awk '{ print $3 }')
major=${version%%.*}
rest=${version#*.}
minor=${rest%%.*}
capable=false
case "$major:$minor" in
    '' | *[!0-9:]* ) ;;
    * ) if [ "$major" -gt 2 ] || { [ "$major" -eq 2 ] && [ "$minor" -ge 34 ]; }; then capable=true; fi ;;
esac
verification=unavailable
if [ "$capable" = true ] && [ -n "$tip" ]; then
    if GIT_CONFIG_NOSYSTEM=1 git -C "$registry" -c gpg.format=ssh -c gpg.ssh.allowedSignersFile="$signers" verify-commit "$tip" >/dev/null 2>&1; then
        verification=passed
    else
        verification=failed
    fi
fi
printf 'registry_remote=%s\\n' "$remote"
printf 'registry_ref=%s\\n' "$ref"
printf 'runtime_revision=%s\\n' "$runtime"
printf 'allowed_signer_principal=%s\\n' "$principal"
printf 'allowed_signer_fingerprint=%s\\n' "$fingerprint"
printf 'allowed_signers_sha256=%s\\n' "$digest"
printf 'git_ssh_signature_capable=%s\\n' "$capable"
printf 'fetched_tip=%s\\n' "$tip"
printf 'signature_verification=%s\\n' "$verification"
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
    failure: OperationFailure | None = None


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
            failure=_failure_diagnostics(values, include_unit=False) if state == "failed" else None,
        )

    def inspect_verifier(self, request: ApplyRequest) -> HostVerifierDiagnostic:
        """Read only the fixed, public facts behind V2 Git signature verification."""
        return _parse_verifier_diagnostics(self._run(request, _VERIFIER_REMOTE, verifier=True))

    def _run(
        self,
        request: ApplyRequest,
        script: str,
        invocation: str | None = None,
        *,
        verifier: bool = False,
    ) -> dict[str, Any]:
        remote_args = (
            ["verifier"]
            if verifier
            else [request.unit]
            if invocation is None
            else [request.unit, invocation]
        )
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
        if verifier:
            return _parse_verifier_output(completed.stdout)
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
            failure=_failure_diagnostics(values) if state == "failed" else None,
        )


def operation_provider() -> OperationProvider:
    """Return the sole supported host-apply transport."""
    return SshOperationProvider()


def inspect_verifier(request: ApplyRequest) -> HostVerifierDiagnostic:
    """Return fixed, read-only V2 verifier facts over the declared SSH transport."""
    return SshOperationProvider().inspect_verifier(request)


def resolve_apply_request(registry_path: Path, host: Any) -> ApplyRequest:
    """Resolve a manifest-declared V2 SSH reconcile operation."""
    if not registry_path.is_dir():
        raise _registry_failure("Host apply requires a directory registry checkout", registry_path)
    manifest_path = registry_path / host.uuid / "manifest.yml"
    manifest = _read_mapping(manifest_path, "Host apply manifest is missing or invalid")
    host_data = (
        manifest.get("hosts", {}).get(host.uuid)
        if isinstance(manifest.get("hosts"), dict)
        else None
    )
    if not isinstance(host_data, dict) or host_data.get("canonical_name") != host.canonical_name:
        raise _registry_failure("Host apply manifest does not match the target host", manifest_path)
    manifest_request = _manifest_request(host.uuid, host.canonical_name, host_data, manifest_path)
    if manifest_request is not None:
        return manifest_request
    return _contract_request(registry_path, host)


def _manifest_request(
    host_uuid: str, canonical_name: str, data: dict[str, Any], path: Path
) -> ApplyRequest | None:
    if _MANIFEST_V2_APPLY_FIELDS.isdisjoint(data):
        return None
    if data.get("self_deploy_v2_reconcile_enabled") is not True:
        raise _registry_failure("Host apply manifest does not enable V2 reconcile", path)
    if data.get("self_deploy_v2_reconcile_packaged") is not True:
        raise _registry_failure("Host apply manifest does not package V2 reconcile", path)
    if data.get("self_deploy_v2_promotion_policy_enabled") is not True:
        raise _registry_failure("Host apply manifest does not enable V2 promotion policy", path)
    channel = data.get("self_deploy_v2_promotion_channel")
    if not isinstance(channel, str) or _CHANNEL.fullmatch(channel) is None:
        raise _registry_failure("Host apply manifest V2 promotion channel is invalid", path)
    address = data.get("tailscale_ip")
    if not isinstance(address, str):
        raise _registry_failure("Host apply manifest Tailscale address is invalid", path)
    try:
        parsed_address = ipaddress.ip_address(address)
    except ValueError:
        raise _registry_failure("Host apply manifest Tailscale address is invalid", path) from None
    if not isinstance(
        parsed_address, ipaddress.IPv4Address
    ) or parsed_address not in ipaddress.ip_network("100.64.0.0/10"):
        raise _registry_failure(
            "Host apply manifest Tailscale address is outside the tailnet range", path
        )
    fingerprint = _normalize_manifest_fingerprint(
        data.get("self_deploy_v2_promotion_host_fingerprint")
    )
    if fingerprint is None:
        raise _registry_failure("Host apply manifest SSH fingerprint is invalid", path)
    reconcile = data.get("reconcile")
    if reconcile is not None and (
        not isinstance(reconcile, dict) or reconcile.get("unit") != _UNIT
    ):
        raise _registry_failure("Host apply manifest reconcile unit is invalid", path)
    return ApplyRequest(host_uuid, canonical_name, address, 22, "root", fingerprint, _UNIT)


def _contract_request(registry_path: Path, host: Any) -> ApplyRequest:
    """Accept the pre-manifest operations contract for existing consumers."""
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
    if reconcile is not None and (
        not isinstance(reconcile, dict) or reconcile.get("unit") != _UNIT
    ):
        raise _registry_failure("Host apply contract reconcile unit is invalid", contract_path)
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


def _normalize_manifest_fingerprint(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parts = value.split()
    if len(parts) != 2 or parts[0] != "ssh-rsa" or _FINGERPRINT.fullmatch(parts[1]) is None:
        return None
    return parts[1]


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
    values: dict[str, Any] = {"journal": [], "journal_text": []}
    journal_started = False
    for line in stdout.splitlines():
        if line == _JOURNAL_SEPARATOR:
            journal_started = True
            continue
        key, separator, value = line.partition("=")
        if (
            not journal_started
            and separator
            and key in {"InvocationID", "ActiveState", "Result", "ExecMainStatus"}
        ):
            values[key] = value
            continue
        values["journal_text"].append(line)
        try:
            document = yaml.safe_load(line)
        except yaml.YAMLError:
            continue
        if isinstance(document, dict):
            values["journal"].append(document)
    return values


def _parse_verifier_output(stdout: str) -> dict[str, Any]:
    """Accept only the fixed public facts emitted by the verifier probe."""
    accepted = {
        "registry_remote",
        "registry_ref",
        "runtime_revision",
        "allowed_signer_principal",
        "allowed_signer_fingerprint",
        "allowed_signers_sha256",
        "git_ssh_signature_capable",
        "fetched_tip",
        "signature_verification",
    }
    values: dict[str, Any] = {}
    for line in stdout.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key not in accepted:
            continue
        if key in values:
            raise _provider_failure("Declared host returned invalid verifier diagnostics")
        values[key] = value
    return values


def _parse_verifier_diagnostics(values: dict[str, Any]) -> HostVerifierDiagnostic:
    """Validate public facts before they cross the CLI boundary."""
    try:
        unavailable: list[VerifierUnavailableFact] = []

        remote = _optional_verifier_value(values, "registry_remote", unavailable)
        if remote is not None:
            parsed = urlsplit(remote)
            if (
                not parsed.scheme
                or not parsed.hostname
                or (
                    parsed.username is not None
                    and (parsed.scheme != "ssh" or parsed.username != "git")
                )
                or parsed.password is not None
                or parsed.query
                or parsed.fragment
                or any(character.isspace() or ord(character) < 32 for character in remote)
            ):
                raise ValueError

        registry_ref = _optional_verifier_value(values, "registry_ref", unavailable)
        if registry_ref is not None and registry_ref != "refs/heads/main":
            raise ValueError

        runtime = _optional_verifier_value(values, "runtime_revision", unavailable)
        if runtime is not None and _GIT_SHA.fullmatch(runtime) is None:
            raise ValueError

        signer = _optional_allowed_signer(values, unavailable)

        capable = _optional_verifier_value(values, "git_ssh_signature_capable", unavailable)
        if capable is not None and capable not in {"true", "false"}:
            raise ValueError

        tip = _optional_verifier_value(values, "fetched_tip", unavailable)
        if tip is not None and _GIT_SHA.fullmatch(tip) is None:
            raise ValueError

        verification = _optional_verifier_value(values, "signature_verification", unavailable)
        if verification is not None and verification not in {"passed", "failed", "unavailable"}:
            raise ValueError
        return HostVerifierDiagnostic(
            registry_remote=remote,
            registry_ref=cast(Literal["refs/heads/main"] | None, registry_ref),
            runtime_revision=runtime,
            allowed_signer=signer,
            git_ssh_signature_capable=None if capable is None else capable == "true",
            fetched_tip=tip,
            signature_verification=cast(
                Literal["passed", "failed", "unavailable"] | None, verification
            ),
            unavailable=unavailable,
        )
    except (TypeError, ValueError):
        raise _provider_failure("Declared host returned invalid verifier diagnostics") from None


def _optional_verifier_value(
    values: dict[str, Any], key: VerifierUnavailableFact, unavailable: list[VerifierUnavailableFact]
) -> str | None:
    value = values.get(key)
    if value is None:
        unavailable.append(key)
        return None
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _optional_allowed_signer(
    values: dict[str, Any], unavailable: list[VerifierUnavailableFact]
) -> AllowedSignerDiagnostic | None:
    keys = (
        "allowed_signer_principal",
        "allowed_signer_fingerprint",
        "allowed_signers_sha256",
    )
    present = [key in values for key in keys]
    if not any(present):
        unavailable.append("allowed_signer")
        return None
    if not all(present):
        raise ValueError
    return AllowedSignerDiagnostic(
        principal=_required_signer_principal(values, "allowed_signer_principal"),
        fingerprint=_required_fingerprint(values, "allowed_signer_fingerprint"),
        sha256=_required_sha256(values, "allowed_signers_sha256"),
    )


def _required_text(values: dict[str, Any], key: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError
    return value


def _required_sha(values: dict[str, Any], key: str) -> str:
    value = _required_text(values, key)
    if _GIT_SHA.fullmatch(value) is None:
        raise ValueError
    return value


def _required_sha256(values: dict[str, Any], key: str) -> str:
    value = _required_text(values, key)
    if _SHA256.fullmatch(value) is None:
        raise ValueError
    return value


def _required_signer_principal(values: dict[str, Any], key: str) -> str:
    value = _required_text(values, key)
    if _SIGNER_PRINCIPAL.fullmatch(value) is None:
        raise ValueError
    return value


def _required_fingerprint(values: dict[str, Any], key: str) -> str:
    value = _required_text(values, key)
    if _FINGERPRINT.fullmatch(value) is None:
        raise ValueError
    return value


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


def _failure_diagnostics(values: dict[str, Any], *, include_unit: bool = True) -> OperationFailure:
    unit: OperationUnitFailure | None = None
    if include_unit:
        unit = OperationUnitFailure(
            active_state=_diagnostic_text(values.get("ActiveState")),
            result=_diagnostic_text(values.get("Result")),
            exec_main_status=_diagnostic_status(values.get("ExecMainStatus")),
        )
    return OperationFailure(unit=unit, journal=_journal_diagnostics(values))


def _journal_diagnostics(values: dict[str, Any]) -> list[str]:
    records = values.get("journal")
    diagnostics = _sanitize_journal(records)
    journal_text = values.get("journal_text")
    if diagnostics or not isinstance(journal_text, list) or not journal_text:
        return diagnostics
    return ["unstructured journal output omitted"]


def _sanitize_journal(records: object) -> list[str]:
    """Return only canonical producer failure fields, never raw journal values."""
    if not isinstance(records, list):
        return []
    sanitized: list[str] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("ok") is not False:
            continue
        code = record.get("error_code")
        stage = record.get("error_stage")
        retryable = record.get("retryable")
        if (
            not isinstance(code, str)
            or _DIAGNOSTIC_CODE.fullmatch(code) is None
            or not isinstance(stage, str)
            or stage not in _DIAGNOSTIC_STAGES
            or type(retryable) is not bool
        ):
            continue
        sanitized.append(f"code: {code}")
        sanitized.append(f"stage: {stage}")
        sanitized.append(f"retryable: {str(retryable).lower()}")
        if len(sanitized) >= _MAX_FAILURE_JOURNAL_LINES:
            return sanitized[:_MAX_FAILURE_JOURNAL_LINES]
    return sanitized


def _diagnostic_text(value: object) -> str:
    return value if isinstance(value, str) and value else "unknown"


def _diagnostic_status(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return -1


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
    observed_fingerprints: set[str] = set()
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
        observed_fingerprint = _ssh_fingerprint(fingerprint.stdout)
        if fingerprint.returncode == 0 and observed_fingerprint is not None:
            observed_fingerprints.add(observed_fingerprint)
        if fingerprint.returncode == 0 and request.host_key_fingerprint == observed_fingerprint:
            matching.append(line)
    if not matching:
        raise _provider_failure(
            "Declared host SSH key does not match its fingerprint",
            details={"observed_fingerprints": sorted(observed_fingerprints)},
        )
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


def _provider_failure(message: str, *, details: dict[str, Any] | None = None) -> CliFailure:
    return CliFailure(
        code=ErrorCode.PROVIDER_UNAVAILABLE,
        message=message,
        exit_code=ExitCode.PROVIDER_ERROR,
        fix="Retry the host apply or inspect the host-local reconcile unit",
        details=details or {},
    )
