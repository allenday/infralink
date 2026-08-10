"""Signed runtime boundary and executable for the local Doctor agent."""

from __future__ import annotations

import json
import os
import pwd
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import NoReturn

import click

from infralink.host_readiness import HostReadinessProbe
from infralink.local_doctor import (
    LatestResultStore,
    LocalDoctorCollector,
    LocalDoctorResult,
    SshAllowedSignersTrustRoot,
    _prometheus_metrics,
    _verify_ssh_signature,
    serve_latest_result,
)

RUNTIME_SCHEMA_VERSION = "infralink.local-doctor-runtime/v1"
AGENT_SCHEMA_VERSION = "infralink.local-doctor-agent/v1"
_MAX_SIGNATURE_LENGTH = 4096


@dataclass(frozen=True)
class RuntimeOutputRoots:
    """Fixed least-privilege filesystem roots owned by the local agent."""

    state_root: Path
    metrics_root: Path
    runtime_root: Path


DEFAULT_OUTPUT_ROOTS = RuntimeOutputRoots(
    state_root=Path("/var/lib/infralink/local-doctor"),
    metrics_root=Path("/var/lib/node-exporter/textfile_collector"),
    runtime_root=Path("/etc/infralink/local-doctor"),
)


@dataclass(frozen=True)
class RuntimeConfigTrustRoot:
    """Concrete SSH allowed-signers file used to verify generated runtime input."""

    allowed_signers_path: Path
    principal: str = "infralink-local-doctor"
    namespace: str = "infralink.local-doctor-runtime"

    def __post_init__(self) -> None:
        if not self.principal or not self.namespace:
            raise ValueError("runtime allowed signers trust root is invalid")


@dataclass(frozen=True)
class LocalDoctorRuntimeConfig:
    """Bounded host-local runtime configuration from a signed generated artifact."""

    canonical_name: str
    freshness_seconds: int
    state_path: Path
    metrics_path: Path
    firewall_declaration_path: Path
    firewall_allowed_signers_path: Path
    require_reconcile: bool
    http_address: str
    http_port: int

    @classmethod
    def from_dict(
        cls, value: object, *, output_roots: RuntimeOutputRoots = DEFAULT_OUTPUT_ROOTS
    ) -> LocalDoctorRuntimeConfig:
        if not isinstance(value, dict) or set(value) != {
            "canonical_name",
            "freshness_seconds",
            "state_path",
            "metrics_path",
            "firewall_declaration_path",
            "firewall_allowed_signers_path",
            "require_reconcile",
            "http_address",
            "http_port",
        }:
            raise ValueError("local Doctor runtime config is invalid")
        canonical_name = value["canonical_name"]
        freshness_seconds = value["freshness_seconds"]
        path_values = (
            value["state_path"],
            value["metrics_path"],
            value["firewall_declaration_path"],
            value["firewall_allowed_signers_path"],
        )
        if (
            not isinstance(canonical_name, str)
            or not canonical_name
            or len(canonical_name) > 253
            or any(character.isspace() for character in canonical_name)
            or type(freshness_seconds) is not int
            or not 1 <= freshness_seconds <= 86_400
            or not all(isinstance(path, str) and Path(path).is_absolute() for path in path_values)
            or type(value["require_reconcile"]) is not bool
            or not isinstance(value["http_address"], str)
            or not value["http_address"]
            or len(value["http_address"]) > 253
            or any(character.isspace() for character in value["http_address"])
            or type(value["http_port"]) is not int
            or not 1 <= value["http_port"] <= 65_535
            or not _within_root(Path(value["state_path"]), output_roots.state_root)
            or not _within_root(Path(value["metrics_path"]), output_roots.metrics_root)
            or not _within_root(Path(value["firewall_declaration_path"]), output_roots.runtime_root)
            or not _within_root(
                Path(value["firewall_allowed_signers_path"]), output_roots.runtime_root
            )
        ):
            raise ValueError("local Doctor runtime config is invalid")
        return cls(
            canonical_name=canonical_name,
            freshness_seconds=freshness_seconds,
            state_path=Path(value["state_path"]),
            metrics_path=Path(value["metrics_path"]),
            firewall_declaration_path=Path(value["firewall_declaration_path"]),
            firewall_allowed_signers_path=Path(value["firewall_allowed_signers_path"]),
            require_reconcile=value["require_reconcile"],
            http_address=value["http_address"],
            http_port=value["http_port"],
        )


def _within_root(path: Path, root: Path) -> bool:
    """Accept only a file below a fixed root after resolving existing symlinks."""
    try:
        return path.resolve().is_relative_to(root.resolve()) and path.resolve() != root.resolve()
    except OSError:
        return False


def load_signed_runtime_config(
    path: Path,
    *,
    trust_root: RuntimeConfigTrustRoot,
    output_roots: RuntimeOutputRoots = DEFAULT_OUTPUT_ROOTS,
) -> LocalDoctorRuntimeConfig:
    """Verify a generated runtime artifact before accepting any local paths or settings."""
    if not trust_root.allowed_signers_path.is_file():
        raise ValueError("runtime allowed signers trust root is unavailable")
    try:
        envelope = json.loads(path.read_bytes())
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError("local Doctor runtime config is invalid") from error
    if not isinstance(envelope, dict) or set(envelope) != {"schema_version", "config", "signature"}:
        raise ValueError("local Doctor runtime config is invalid")
    config = envelope["config"]
    signature = envelope["signature"]
    if (
        envelope["schema_version"] != RUNTIME_SCHEMA_VERSION
        or not isinstance(config, dict)
        or not isinstance(signature, str)
        or not signature
        or len(signature) > _MAX_SIGNATURE_LENGTH
    ):
        raise ValueError("local Doctor runtime config is invalid")
    payload = json.dumps(config, sort_keys=True, separators=(",", ":")).encode()
    firewall_trust_root = SshAllowedSignersTrustRoot(
        trust_root.allowed_signers_path,
        principal=trust_root.principal,
        namespace=trust_root.namespace,
    )
    if not _verify_ssh_signature(payload, signature, firewall_trust_root):
        raise ValueError("local Doctor runtime config signature is invalid")
    return LocalDoctorRuntimeConfig.from_dict(config, output_roots=output_roots)


def collect_local_readiness_probe() -> HostReadinessProbe:
    """Gather the same bounded readiness facts locally, without SSH or remote execution."""
    try:
        runtime_v2 = Path("/var/lib/self-deploy-v2/runtime").is_dir() and _unit_exists(
            "self-deploy-v2-reconcile.timer"
        )
        legacy = (
            Path("/opt/infra/scripts/self-deploy.sh").is_file()
            and Path("/etc/cron.d/self-deploy").is_file()
        )
        mode = "v2_reconcile" if runtime_v2 else "legacy_pull" if legacy else None
        enabled, active = _timer_state(runtime_v2)
        reconcile = _reconcile_state(runtime_v2)
        return HostReadinessProbe(
            reachable=True,
            hostname=_read_hostname(),
            machine_id=_read_optional("/etc/machine-id"),
            commands={
                name: shutil.which(name) is not None
                for name in ("git", "docker", "tailscale", "jq", "bws")
            },
            devops_account=_devops_account_exists(),
            devops_authorized_access=Path("/home/devops/.ssh/authorized_keys").is_file()
            and Path("/home/devops/.ssh/authorized_keys").stat().st_size > 0,
            bws_config=_environment_has_bws_token(),
            self_deploy_dependencies=_python_dependencies_present(),
            self_deploy_runtime=mode is not None,
            self_deploy_timer_enabled=enabled,
            self_deploy_timer_active=active,
            error=None,
            self_deploy_mode=mode,
            registry_layout=_registry_layout(),
            self_deploy_reconcile_result=reconcile[0],
            self_deploy_reconcile_exit_status=reconcile[1],
            self_deploy_reconcile_active_state=reconcile[2],
            self_deploy_reconcile_sub_state=reconcile[3],
            self_deploy_reconcile_exit_timestamp_monotonic=reconcile[4],
        )
    except OSError:
        return HostReadinessProbe(
            False, None, None, {}, False, False, False, False, False, False, "local_probe_failed"
        )


def _read_hostname() -> str | None:
    return _read_optional("/etc/hostname") or os.uname().nodename or None


def _read_optional(path: str) -> str | None:
    try:
        value = Path(path).read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value or None


def _devops_account_exists() -> bool:
    try:
        pwd.getpwnam("devops")
    except KeyError:
        return False
    return True


def _environment_has_bws_token() -> bool:
    try:
        lines = Path("/etc/environment").read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    return any(
        line.strip().startswith("BWS_ACCESS_TOKEN=") and line.partition("=")[2] for line in lines
    )


def _python_dependencies_present() -> bool:
    completed = subprocess.run(
        ["python3", "-c", "import yaml, jinja2"], capture_output=True, check=False, shell=False
    )
    return completed.returncode == 0


def _unit_exists(unit: str) -> bool:
    completed = subprocess.run(
        ["systemctl", "cat", unit], capture_output=True, check=False, shell=False
    )
    return completed.returncode == 0


def _systemctl_state(command: str, unit: str) -> bool:
    completed = subprocess.run(
        ["systemctl", command, unit], capture_output=True, check=False, shell=False
    )
    return completed.returncode == 0


def _timer_state(runtime_v2: bool) -> tuple[bool, bool]:
    if runtime_v2:
        return (
            _systemctl_state("is-enabled", "self-deploy-v2-reconcile.timer"),
            _systemctl_state("is-active", "self-deploy-v2-reconcile.timer"),
        )
    legacy = Path("/etc/cron.d/self-deploy").is_file()
    return legacy, legacy


def _reconcile_state(
    runtime_v2: bool,
) -> tuple[str | None, int | None, str | None, str | None, int | None]:
    if not runtime_v2 or not _unit_exists("self-deploy-v2-reconcile.service"):
        return None, None, None, None, None
    names = (
        "Result",
        "ExecMainStatus",
        "ActiveState",
        "SubState",
        "ExecMainExitTimestampMonotonic",
    )
    values = tuple(_systemctl_show(name) for name in names)
    try:
        exit_status = int(values[1]) if values[1] else None
        timestamp = int(values[4]) if values[4] else None
    except ValueError:
        exit_status, timestamp = None, None
    return values[0] or None, exit_status, values[2] or None, values[3] or None, timestamp


def _systemctl_show(name: str) -> str:
    completed = subprocess.run(
        ["systemctl", "show", "self-deploy-v2-reconcile.service", "-p", name, "--value"],
        text=True,
        capture_output=True,
        check=False,
        shell=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _registry_layout() -> str:
    v2, legacy = Path("/var/lib/infralink/registry"), Path("/opt/infra/registry")
    if v2.is_symlink() or legacy.is_symlink():
        return "unsafe"
    if (v2 / ".git").is_dir() and not legacy.exists():
        return "v2_managed"
    if (legacy / ".git").is_dir() and not v2.exists():
        return "legacy_nested"
    if not v2.exists() and not legacy.exists():
        return "missing"
    return "unsafe"


def write_prometheus_textfile(path: Path, result: LocalDoctorResult) -> None:
    """Atomically replace the fixed-cardinality Prometheus textfile after state persistence."""
    path.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(_prometheus_metrics(result, now=datetime.now(timezone.utc)))
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _snapshot(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_snapshot(path: Path, content: bytes | None) -> None:
    if content is None:
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        return
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.restore.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o640)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def collect_and_persist(runtime: LocalDoctorRuntimeConfig) -> LocalDoctorResult:
    """Publish state and textfile as one recoverable collection operation."""
    result = LocalDoctorCollector(
        clock=lambda: datetime.now(timezone.utc), freshness_seconds=runtime.freshness_seconds
    ).collect(
        canonical_name=runtime.canonical_name,
        probe=collect_local_readiness_probe(),
        firewall_declaration_path=runtime.firewall_declaration_path,
        firewall_trust_root=SshAllowedSignersTrustRoot(runtime.firewall_allowed_signers_path),
        require_reconcile=runtime.require_reconcile,
    )
    state_store = LatestResultStore(runtime.state_path)
    previous_state = _snapshot(runtime.state_path)
    previous_metrics = _snapshot(runtime.metrics_path)
    try:
        state_store.write(result)
        write_prometheus_textfile(runtime.metrics_path, result)
    except BaseException:
        _restore_snapshot(runtime.state_path, previous_state)
        _restore_snapshot(runtime.metrics_path, previous_metrics)
        raise
    return result


def _emit(
    ok: bool,
    path: list[str],
    result: dict[str, object] | None = None,
    *,
    flags: list[str],
    error: str | None = None,
) -> None:
    argv = ["infralink-local-doctor", *path, *flags]
    payload: dict[str, object] = {
        "schema_version": AGENT_SCHEMA_VERSION,
        "ok": ok,
        "command": {
            "raw": shlex.join(argv),
            "parsed": {"path": path, "args": {}, "flags": flags},
        },
    }
    if ok:
        payload["result"] = result or {}
    else:
        payload["error"] = {
            "code": "runtime_config_invalid",
            "message": error or "runtime config invalid",
        }
    click.echo(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def _runtime_or_error(config: Path, allowed_signers: Path) -> LocalDoctorRuntimeConfig:
    return load_signed_runtime_config(
        config,
        trust_root=RuntimeConfigTrustRoot(allowed_signers),
        output_roots=DEFAULT_OUTPUT_ROOTS,
    )


@click.group()
def main() -> None:
    """Run host-local persisted Doctor evidence collection and serving."""


@main.command()
@click.option("--config", type=click.Path(path_type=Path), required=True)
@click.option("--allowed-signers", type=click.Path(path_type=Path), required=True)
def collect(config: Path, allowed_signers: Path) -> None:
    """Verify runtime input, collect local readiness, then atomically persist it."""
    try:
        runtime = _runtime_or_error(config, allowed_signers)
        result = collect_and_persist(runtime)
    except (OSError, ValueError) as error:
        _emit(
            False,
            ["collect"],
            flags=["--config", str(config), "--allowed-signers", str(allowed_signers)],
            error=str(error),
        )
        raise click.exceptions.Exit(2) from error
    _emit(
        True,
        ["collect"],
        {"status": result.status},
        flags=["--config", str(config), "--allowed-signers", str(allowed_signers)],
    )


@main.command()
@click.option("--config", type=click.Path(path_type=Path), required=True)
@click.option("--allowed-signers", type=click.Path(path_type=Path), required=True)
def serve(config: Path, allowed_signers: Path) -> NoReturn:
    """Serve persisted evidence only; collection remains a separate scheduled operation."""
    try:
        runtime = _runtime_or_error(config, allowed_signers)
    except ValueError as error:
        _emit(
            False,
            ["serve"],
            flags=["--config", str(config), "--allowed-signers", str(allowed_signers)],
            error=str(error),
        )
        raise click.exceptions.Exit(2) from error
    server = serve_latest_result(
        runtime.http_address,
        runtime.http_port,
        LatestResultStore(runtime.state_path),
        clock=lambda: datetime.now(timezone.utc),
    )
    server.serve_forever()
    raise AssertionError("unreachable")


def run() -> None:
    main()
