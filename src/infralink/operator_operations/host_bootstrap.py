"""Transport-neutral declared host bootstrap implementation.

This module owns bootstrap planning, validation, execution, and diagnostics.
Public transports import these helpers; it must not import the Click command
module, which prevents either transport from becoming the authority.
"""

from __future__ import annotations

import base64
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlsplit, urlunsplit

import yaml

from infralink.cli.actions import action
from infralink.cli.contracts import (
    Action,
    HostBootstrapAction,
    HostBootstrapRequest,
    HostControllerBootstrapState,
    HostReadinessCheck,
    HostReadinessResult,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.host_readiness import evaluate_host_readiness
from infralink.host_readiness import HostReadinessProbe
from infralink.host_transport import SshReadinessTransport

if TYPE_CHECKING:
    from infralink.cli.main import Context


_CONTROL_ROOT = Path(os.environ.get("INFRALINK_CONTROL_ROOT", "/opt/infra"))
_BOOTSTRAP_EXECUTOR_ROOT = Path("/app")
_CONTROLLER_REFRESH_PLAYBOOK = "ansible/playbooks/infralink_controller_refresh.yml"
_CONTROLLER_REFRESH_SOURCE_REMOTE = "https://github.com/relax-dot-gg/infra-management.git"


def execute_bootstrap(ctx: Any, request: Any) -> tuple[Any, list[Action], bool]:
    """Run one typed bootstrap request; transports only adapt input and output."""
    target = ctx.registry.get(request.host_id)
    if target is None:
        from infralink.cli.queries import entity_not_found

        raise entity_not_found("host", request.host_id)
    address = _bootstrap_tailnet_address(target, request.ssh_host)
    projects = _bootstrap_declared_bws_projects(ctx, target)
    controller_state = _controller_bootstrap_state(ctx.hosts_path, target)
    if request.bws_token is not None:
        _validate_bootstrap_bws_access(
            ctx,
            projects,
            request.bws_token,
            controller_secret=controller_state.registry_read_identity_secret,
        )
    with _bootstrap_pinned_transport(ctx, target, address) as transport:
        probe = transport.probe(address)
        _require_remote_tailnet_identity(target, probe, address)
        readiness = evaluate_bootstrap_readiness(target, probe, address=address)
        if request.bws_token is None:
            readiness = _readiness_with_bws_token_required(readiness)
        automated_actions = _bootstrap_executor_actions(readiness)
        if request.apply and automated_actions:
            readiness = _apply_bootstrap_request(
                ctx,
                target,
                address,
                automated_actions,
                controller_state,
                request.bws_token,
                transport.known_hosts,
            )
    from infralink.cli.contracts import DoctorTarget, HostBootstrapPlanResult

    result = HostBootstrapPlanResult(
        host=DoctorTarget(type="host", id=target.uuid, canonical_name=target.canonical_name),
        readiness=readiness,
    )
    actions = _bootstrap_plan_actions(
        ctx, target, address, readiness, bws_token_supplied=request.bws_token is not None
    )
    return result, actions, readiness.ready or request.plan


def _root_source_argv(ctx: Context) -> list[str]:
    argv = ["infralink"]
    if ctx.output_explicit:
        argv.extend(["--output", ctx.output])
    if ctx.registry_path is not None:
        argv.extend(["--registry", str(ctx.registry_path)])
    if ctx.edges_path is not None:
        argv.extend(["--edges", str(ctx.edges_path)])
    return argv


def _registry_checkout_root(path: Path | None) -> Path | None:
    """Resolve the selected registry checkout root without importing Click."""
    if path is None or not path.is_dir():
        return None
    if (path / "hosts").is_dir():
        return path
    if path.name == "hosts" and (path.parent / "hosts").is_dir():
        return path.parent
    return None


def _action_argv_prefix() -> list[str]:
    return ["infralink"]


def _isolated_git_environment() -> dict[str, str]:
    """Run controller Git with only the managed root credential store enabled."""
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": "/root",
        "LANG": "C",
        "LC_ALL": "C",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_COUNT": "3",
        "GIT_CONFIG_KEY_0": "credential.helper",
        "GIT_CONFIG_VALUE_0": "",
        "GIT_CONFIG_KEY_1": "credential.helper",
        "GIT_CONFIG_VALUE_1": "store",
        "GIT_CONFIG_KEY_2": "credential.useHttpPath",
        "GIT_CONFIG_VALUE_2": "true",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_OPTIONAL_LOCKS": "0",
    }


def _controller_remote_identity(remote: str) -> str:
    """Compare HTTPS controller remotes without retaining embedded credentials."""
    parsed = urlsplit(remote)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return remote
    host = parsed.hostname
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    return urlunsplit((parsed.scheme, host, parsed.path, "", ""))


def _bootstrap_plan_actions(
    ctx: Context,
    target: Any,
    address: str,
    readiness: HostReadinessResult,
    *,
    bws_token_supplied: bool,
) -> list[Action]:
    actions = [
        action(
            "reinspect-readiness",
            [
                *_root_source_argv(ctx),
                "host",
                "bootstrap",
                target.uuid,
                "--ssh-host",
                address,
            ],
            "Reinspect live host readiness",
        )
    ]
    if not bws_token_supplied and _bootstrap_apply_handoff_is_safe(readiness):
        actions.append(_bootstrap_bws_token_apply_action(ctx, target, address))
    return actions


_BOOTSTRAP_HANDOFF_ACTION_IDS = frozenset(
    {
        "initialize_machine_id",
        "install_git",
        "install_docker",
        "install_jq",
        "install_bws_cli",
        "install_self_deploy_dependencies",
        "bootstrap_infralink_controller",
    }
)

_BOOTSTRAP_EXECUTOR_ACTION_IDS = _BOOTSTRAP_HANDOFF_ACTION_IDS - {
    "initialize_machine_id",
}

_BOOTSTRAP_CONTROLLER_BACKED_CHECK_IDS = frozenset(
    {
        "bws_config",
        "self_deploy_runtime",
        "self_deploy_timer",
        "self_deploy_reconcile",
    }
)


def _bootstrap_apply_handoff_is_safe(readiness: HostReadinessResult) -> bool:
    """Advertise stdin apply only when every failed gate is executor-backed."""
    required_failures = {
        check.id for check in readiness.checks if check.required and not check.passed
    }
    executor_checks = {
        action.check_id
        for action in readiness.actions
        if action.id in _BOOTSTRAP_HANDOFF_ACTION_IDS
    }
    return bool(required_failures) and required_failures <= (
        executor_checks | _BOOTSTRAP_CONTROLLER_BACKED_CHECK_IDS | {"bws_token"}
    )


def _bootstrap_bws_token_apply_action(ctx: Context, target: Any, address: str) -> Action:
    apply_argv = [
        *_root_source_argv(ctx),
        "host",
        "bootstrap",
        target.uuid,
        "--ssh-host",
        address,
        "--bws-token-stdin",
        "--apply",
    ]
    return Action(
        rel="apply",
        argv=[],
        command=(f"printf '%s\\n' \"$HOST_BWS_TOKEN\" | {shlex.join(apply_argv)}"),
        description="Apply the declared bootstrap with the host machine BWS token.",
        safe=False,
    )


def _bootstrap_tailnet_address(target: Any, ssh_host: str) -> str:
    """Accept only the exact registry-owned Tailnet SSH target."""
    from agent_surface import OperationError

    from infralink.operator_surface import (
        DoctorBootstrapPlanRequest,
        DoctorBootstrapPlanResult,
        doctor_host_bootstrap_plan,
    )

    try:
        operation = cast(
            DoctorBootstrapPlanResult,
            doctor_host_bootstrap_plan(
                DoctorBootstrapPlanRequest(
                    host_ref=str(target.uuid),
                    ssh_host=ssh_host,
                    declared_ssh_host=str(target.tailscale_ip),
                )
            ),
        )
        return operation.ssh_host
    except OperationError as error:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message=error.message,
            exit_code=ExitCode.INPUT_ERROR,
            fix=error.fix or "Declare the host Tailnet IPv4 and pass it with --ssh-host",
            details=error.details[0] if error.details else {"host": target.uuid},
        ) from None


def _bootstrap_declared_bws_projects(ctx: Context, target: Any) -> tuple[str, ...]:
    """Resolve the new explicit BWS access contract, without legacy fallbacks."""
    projects = tuple(getattr(target, "bws_projects", ()))
    machine_account = getattr(target, "bws_machine_account", None)
    if not projects or not isinstance(machine_account, str) or not machine_account.strip():
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host bootstrap requires bws_projects and bws_machine_account",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare the host machine account and one or more canonical bws_projects",
            details={"host": target.uuid},
        )
    if len(set(projects)) != len(projects) or any(
        not isinstance(item, str) or not item for item in projects
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host bootstrap bws_projects must be a unique nonempty list",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Correct the host bws_projects declaration",
            details={"host": target.uuid},
        )
    return projects


def _read_bootstrap_bws_token() -> str:
    token = sys.stdin.read().strip()
    if not token:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="BWS token standard input was empty",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Pipe the host machine token to host bootstrap --bws-token-stdin --apply",
            details={"requirement": "bws_token_stdin"},
        )
    return token


def _bws_project_catalog(ctx: Context) -> dict[str, str]:
    hosts_path = getattr(ctx, "hosts_path", ctx.registry_path)
    if hosts_path is None:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap BWS validation requires a registry directory checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Use the checked-out registry hosts directory",
        )
    checkout = _registry_checkout_root(ctx.registry_path) or hosts_path.parent
    catalog = checkout / "ansible" / "inventory" / "bws_projects.yml"
    try:
        data = yaml.safe_load(catalog.read_text(encoding="utf-8"))
        raw_projects = data["projects"]
        projects = {
            alias: entry["uuid"]
            for alias, entry in raw_projects.items()
            if isinstance(alias, str)
            and isinstance(entry, dict)
            and isinstance(entry.get("uuid"), str)
        }
    except (OSError, TypeError, KeyError, yaml.YAMLError):
        projects = {}
    if not projects:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap BWS project catalog is unavailable",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Provide a valid ansible/inventory/bws_projects.yml in the selected registry",
        )
    return projects


def _validate_bootstrap_bws_access(
    ctx: Context, aliases: tuple[str, ...], token: str, *, controller_secret: Any | None = None
) -> None:
    catalog = _bws_project_catalog(ctx)
    missing = [alias for alias in aliases if alias not in catalog]
    if missing:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Declared BWS project is absent from the registry catalog",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare only catalogued BWS project aliases",
            details={"projects": missing},
        )
    environment = {**os.environ, "BWS_ACCESS_TOKEN": token}
    for alias in aliases:
        completed = subprocess.run(
            ["bws", "project", "get", catalog[alias], "--output", "none"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
            env=environment,
        )
        if completed.returncode != 0:
            raise CliFailure(
                code=ErrorCode.PROVIDER_AUTHORIZATION_FAILED,
                message="BWS token cannot access a declared bootstrap project",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Grant the declared machine account access to every bws_projects entry",
                details={"project": alias},
            )
    if controller_secret is not None:
        expected_project = catalog.get(controller_secret.project)
        if expected_project is None:
            raise CliFailure(
                code=ErrorCode.CONFIGURATION_REQUIRED,
                message="Controller bootstrap secret project is absent from the registry catalog",
                exit_code=ExitCode.INPUT_ERROR,
                fix="Declare a catalogued project for controller_bootstrap.registry_read_identity_secret",
                details={"project": controller_secret.project},
            )
        completed = subprocess.run(
            ["bws", "secret", "get", controller_secret.id, "--output", "json"],
            text=True,
            capture_output=True,
            check=False,
            timeout=20,
            env=environment,
        )
        try:
            secret = json.loads(completed.stdout) if completed.returncode == 0 else {}
            actual_project = secret.get("projectId")
        except json.JSONDecodeError:
            actual_project = None
        if actual_project != expected_project:
            raise CliFailure(
                code=ErrorCode.PROVIDER_AUTHORIZATION_FAILED,
                message="BWS token cannot read the declared controller registry secret",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Grant the host machine account access to the declared secret project",
                details={"project": controller_secret.project, "secret_id": controller_secret.id},
            )


class _BootstrapProbeTransport:
    """Reuse one pinned SSH observation without issuing a second connection."""

    def __init__(self, probe: HostReadinessProbe) -> None:
        self._probe = probe

    def probe(self, _address: str) -> HostReadinessProbe:
        return self._probe


def evaluate_bootstrap_readiness(
    target: Any, probe: HostReadinessProbe, *, address: str
) -> HostReadinessResult:
    """Evaluate one pinned probe using bootstrap's actionable readiness view."""
    return _bootstrap_operator_readiness(
        evaluate_host_readiness(target, _BootstrapProbeTransport(probe), address=address)
    )


@contextmanager
def _bootstrap_pinned_transport(
    ctx: Context, target: Any, address: str
) -> Iterator[SshReadinessTransport]:
    """Bootstrap uses the same declared SSH identity contract as reconcile."""
    from infralink.cli.operations import _pinned_known_hosts, resolve_apply_request

    if ctx.hosts_path is None or not ctx.hosts_path.is_dir():
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap requires a directory registry checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Use the selected registry hosts directory",
            details={"host": target.uuid},
        )
    _require_bootstrap_ssh_fingerprint(ctx.hosts_path, target)
    request = resolve_apply_request(ctx.hosts_path, target)
    if request.address != address:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap SSH address differs from the declared pinned host identity",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Correct the declared Tailnet address and SSH fingerprint",
            details={"host": target.uuid},
        )
    with _pinned_known_hosts(request) as known_hosts:
        yield SshReadinessTransport(known_hosts=known_hosts)


def _require_bootstrap_ssh_fingerprint(hosts_path: Path, target: Any) -> None:
    """Reject missing initial SSH trust explicitly, before generic apply resolution."""
    manifest_path = hosts_path / target.uuid / "manifest.yml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        manifest = None
    host = (
        manifest.get("hosts", {}).get(target.uuid)
        if isinstance(manifest, dict) and isinstance(manifest.get("hosts"), dict)
        else None
    )
    ssh = host.get("ssh") if isinstance(host, dict) else None
    from infralink.cli.operations import _normalize_manifest_fingerprint

    if (
        not isinstance(ssh, dict)
        or _normalize_manifest_fingerprint(ssh.get("host_key_fingerprint")) is None
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap requires ssh.host_key_fingerprint",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare the host SSH key fingerprint before bootstrap",
            details={"host": target.uuid},
        )


def _require_remote_tailnet_identity(target: Any, probe: HostReadinessProbe, address: str) -> None:
    # The SSH probe intentionally exposes only addresses, never Tailnet auth material.
    if not probe.reachable:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bootstrap SSH connection failed",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Install the authorized key and verify root SSH over the declared Tailnet address",
            details={"host": target.uuid, "ssh_host": address},
        )
    if address not in probe.tailscale_ips:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Remote host is not enrolled at its declared Tailnet address",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Enroll Tailscale manually and correct the host declaration before bootstrap",
            details={"host": target.uuid, "declared_tailscale_ip": address},
        )
    expected_name = target.tailscale_name or target.canonical_name
    if not probe.tailscale_running or probe.tailscale_name != expected_name:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Remote Tailscale identity does not match the declared host",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Start Tailscale and correct its host name before bootstrap",
            details={"host": target.uuid, "expected_name": expected_name},
        )


def _readiness_with_bws_token_required(readiness: HostReadinessResult) -> HostReadinessResult:
    return readiness.model_copy(
        update={
            "ready": False,
            "checks": [
                *readiness.checks,
                HostReadinessCheck(
                    id="bws_token",
                    required=True,
                    passed=False,
                    description="A host machine BWS token was supplied for bootstrap validation.",
                    detail="bws_token_required",
                ),
            ],
            "actions": [
                *readiness.actions,
                HostBootstrapAction(
                    id="provide_bws_token",
                    check_id="bws_token",
                    description="Rerun bootstrap with --bws-token-stdin and provide the host machine token on standard input.",
                ),
            ],
        }
    )


def _bootstrap_operator_readiness(readiness: HostReadinessResult) -> HostReadinessResult:
    """Show only actionable prerequisites plus the one controller action."""
    executable_prerequisites = {
        "establish_root_ssh",
        "correct_host_identity",
        "initialize_machine_id",
        "install_git",
        "install_docker",
        "install_tailscale",
        "install_jq",
        "install_bws_cli",
        "install_self_deploy_dependencies",
    }
    checks = [
        check
        for check in readiness.checks
        if check.id not in {"devops_account", "devops_authorized_access", "registry_layout"}
    ]
    actions = [item for item in readiness.actions if item.id in executable_prerequisites]
    if not readiness.ready:
        actions.append(
            HostBootstrapAction(
                id="bootstrap_infralink_controller",
                check_id="controller_bootstrap",
                description="Install the declared Infralink controller and reconcile timer.",
            )
        )
    return readiness.model_copy(
        update={
            "checks": checks,
            "actions": actions,
            "ready": all(not check.required or check.passed for check in checks),
        }
    )


def _bootstrap_executor_actions(readiness: HostReadinessResult) -> list[str]:
    """Translate only declared bootstrap prerequisites into executor actions."""
    actions = [
        item.id
        for item in readiness.actions
        if item.id in _BOOTSTRAP_EXECUTOR_ACTION_IDS - {"bootstrap_infralink_controller"}
    ]
    if not readiness.ready:
        actions.append("bootstrap_infralink_controller")
    return actions


def _bootstrap_execution_env(token: str | None) -> dict[str, str]:
    if token is None:
        return dict(os.environ)
    return {**os.environ, "BWS_ACCESS_TOKEN": token}


def _apply_bootstrap_request(
    ctx: Context,
    target: Any,
    address: str,
    actions: list[str],
    controller_state: HostControllerBootstrapState,
    token: str | None,
    known_hosts: Path | None,
) -> HostReadinessResult:
    """Run the sole baseline executor with the probe's pinned host identity."""
    if known_hosts is None:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Bootstrap requires a pinned SSH host key",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare ssh.host_key_fingerprint before bootstrap",
            details={"host": target.uuid},
        )
    request = _bootstrap_apply_request(
        ctx, target, actions, address=address, controller_state=controller_state
    )
    with _bootstrap_executor_source(actions) as (source, playbook):
        completed = subprocess.run(
            [
                "ansible-playbook",
                "-vv",
                "-i",
                f"{request.host_address},",
                "-u",
                "root",
                "--ssh-common-args",
                f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts}",
                str(playbook),
                "-e",
                json.dumps(request.ansible_extra_vars(), sort_keys=True),
            ],
            cwd=source,
            text=True,
            capture_output=True,
            check=False,
            env=_bootstrap_execution_env(token),
        )
    if completed.returncode != 0:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Host baseline apply failed",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify the declared bootstrap executor and rerun host bootstrap --apply",
            details=_bootstrap_failure_details(target.uuid, completed, token=token),
        )
    return _bootstrap_operator_readiness(
        evaluate_host_readiness(
            target, SshReadinessTransport(known_hosts=known_hosts), address=address
        )
    )


@contextmanager
def _bootstrap_executor_source(actions: Sequence[str]) -> Iterator[tuple[Path, Path]]:
    """Resolve the bootstrap executor baked into the controller image."""
    manifest_path = "ansible/executors/infralink-host-baseline.json"
    source = _BOOTSTRAP_EXECUTOR_ROOT
    try:
        manifest = json.loads((source / manifest_path).read_text(encoding="utf-8"))
        playbook_path = manifest["playbook"]
        allowed_actions = manifest["allowed_actions"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError):
        manifest = None
        playbook_path = None
        allowed_actions = None
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != "infralink.host-bootstrap-executor/v1"
        or manifest.get("id") != "infra-management-host-baseline"
        or playbook_path != "ansible/playbooks/infralink_host_baseline.yml"
        or not isinstance(allowed_actions, list)
        or not all(isinstance(action_id, str) for action_id in allowed_actions)
        or not set(actions).issubset(allowed_actions)
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Controller bootstrap executor does not support the requested actions",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Publish a controller image with a valid infralink host-bootstrap executor",
            details={"capability": "host_bootstrap"},
        )
    playbook = source / playbook_path
    if not playbook.is_file():
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Controller bootstrap executor playbook is unavailable",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Publish a controller image with the infralink host-bootstrap executor playbook",
            details={"capability": "host_bootstrap"},
        )
    yield source, playbook


def _bootstrap_apply_request(
    ctx: Context,
    target: Any,
    automated_actions: list[str],
    *,
    address: str | None = None,
    controller_state: HostControllerBootstrapState | None = None,
) -> HostBootstrapRequest:
    """Resolve a bounded executor request before any remote mutation begins."""
    address = address or target.tailscale_ip
    if not address:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Host address is required for bootstrap",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare a Tailnet address for the host",
            details={"host": target.uuid},
        )
    try:
        controller_bootstrap: HostControllerBootstrapState | None = controller_state
        return HostBootstrapRequest.model_validate(
            {
                "host_address": str(address),
                "host_uuid": target.uuid,
                "canonical_name": target.canonical_name,
                "bootstrap_actions": automated_actions,
                "controller_bootstrap": controller_bootstrap,
            }
        )
    except CliFailure:
        raise
    except ValueError:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host declaration is incomplete for bootstrap",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare complete V2 bootstrap state and rerun host bootstrap --plan",
            details={"host": target.uuid},
        ) from None


def _controller_bootstrap_state(
    registry_path: Path | None, target: Any
) -> HostControllerBootstrapState:
    if registry_path is None:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Controller bootstrap requires a registry directory checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Provide the registry checkout root with --registry and rerun host bootstrap",
        )
    manifest_path = registry_path / target.uuid / "manifest.yml"
    deployment_path = registry_path / target.uuid / "operations" / "deployment.yml"
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))["hosts"][target.uuid]
        bootstrap = manifest["controller_bootstrap"]
        deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
        image = deployment["controller"]["image"]
        state = HostControllerBootstrapState.model_validate(
            {
                "controller_image": _controller_image_reference(image),
                "registry_read_identity_secret": bootstrap["registry_read_identity_secret"],
                "registry_repo_url": bootstrap["registry_repo_url"],
                "registry_ref": bootstrap["registry_ref"],
            }
        )
    except (OSError, KeyError, TypeError, ValueError, yaml.YAMLError):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host declaration lacks canonical controller bootstrap state",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare controller_bootstrap with registry key reference, repository, and ref",
            details={
                "host": target.uuid,
                "manifest_path": str(manifest_path),
                "deployment_path": str(deployment_path),
                "required_manifest_fields": [
                    "controller_bootstrap.registry_read_identity_secret.project",
                    "controller_bootstrap.registry_read_identity_secret.id",
                    "controller_bootstrap.registry_repo_url",
                    "controller_bootstrap.registry_ref",
                ],
                "required_deployment_fields": [
                    "controller.image.repository",
                    "controller.image.tag",
                    "controller.image.branch (when controller.image.tag is head)",
                ],
            },
            next_actions=[
                action(
                    "inspect",
                    [
                        *_action_argv_prefix(),
                        "--registry",
                        str(registry_path),
                        "host",
                        "show",
                        target.uuid,
                    ],
                    "Inspect the target host declaration",
                )
            ],
        ) from None
    return state


def _controller_image_reference(image: Any) -> str:
    """Use the same head/branch selector semantics for bootstrap and reconcile."""
    if not isinstance(image, dict):
        raise ValueError("controller image must be a mapping")
    repository = image.get("repository")
    tag = image.get("tag")
    if tag == "head":
        tag = image.get("branch", "main")
    if not isinstance(repository, str) or not repository or not isinstance(tag, str) or not tag:
        raise ValueError("controller image reference is incomplete")
    return f"{repository}:{tag}"


def _bootstrap_failure_details(
    host_uuid: str, completed: subprocess.CompletedProcess[str], *, token: str | None
) -> dict[str, Any]:
    """Return bounded, token-safe executor failure evidence."""
    task_headers = list(re.finditer(r"^TASK \[([^\]]+)\]", completed.stdout, re.MULTILINE))
    task_count = min(len(task_headers), 8)
    details: dict[str, Any] = {
        "host": host_uuid,
        "executor": "host_baseline",
        "return_code": completed.returncode,
    }
    if task_count:
        details["task_count"] = task_count
        failed_task: dict[str, str] = {"name": task_headers[-1].group(1)}
        following_output = completed.stdout[task_headers[-1].end() :]
        task_path = re.search(
            r"^task path: (?:/app/)?(ansible/[A-Za-z0-9_./-]+\.yml:\d+)\s*$",
            following_output,
            re.MULTILINE,
        )
        if task_path is not None:
            failed_task["path"] = task_path.group(1)
        details["failed_task"] = failed_task
    nested_failure = _bootstrap_nested_failure_details(completed.stdout, token)
    if nested_failure is not None:
        details["nested_failure"] = nested_failure
    stderr = _sanitize_bootstrap_diagnostic(completed.stderr, token)
    if stderr:
        details["stderr"] = stderr
    return details


def _bootstrap_nested_failure_details(value: str, token: str | None) -> dict[str, Any] | None:
    """Decode the baseline's bounded, already-sanitized nested-controller evidence."""
    match = re.search(r"INFRALINK_BOOTSTRAP_NESTED_FAILURE_B64=([A-Za-z0-9+/=]+)", value)
    if match is None:
        return None
    try:
        payload = json.loads(base64.b64decode(match.group(1), validate=True))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return_code = payload.get("return_code")
    if isinstance(return_code, str) and return_code.isdecimal():
        return_code = int(return_code)
    if not isinstance(return_code, int):
        return None
    result: dict[str, Any] = {"return_code": return_code}
    for key in ("stdout_tail", "stderr_tail"):
        diagnostic = payload.get(key)
        if isinstance(diagnostic, str):
            result[key] = _sanitize_bootstrap_diagnostic(diagnostic, token)
    return result


def _sanitize_bootstrap_diagnostic(value: str, token: str | None) -> str:
    """Keep a short diagnostic tail while removing BWS credentials."""
    if token:
        value = value.replace(token, "[REDACTED]")
    value = re.sub(
        r"(?im)(BWS_ACCESS_TOKEN\s*[=:]\s*)\S+",
        r"\1[REDACTED]",
        value,
    )
    value = re.sub(
        r"(?im)(Authorization:\s*(?:Bearer\s+)?)\S+",
        r"\1[REDACTED]",
        value,
    )
    value = value.strip()
    maximum_length = 1200
    if len(value) > maximum_length:
        return "[truncated]\n" + value[-maximum_length:]
    return value


def _apply_controller_refresh(ctx: Context, target: Any, runtime_revision: str | None) -> None:
    """Run only the pinned controller refresh playbook over declared SSH."""
    from infralink.cli.operations import _pinned_known_hosts, resolve_apply_request

    if ctx.hosts_path is None or not ctx.hosts_path.is_dir():
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Controller refresh requires a directory registry checkout",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Provide the selected registry hosts directory",
            details={"host": target.uuid},
        )
    request = resolve_apply_request(ctx.hosts_path, target)
    resolved_runtime_revision, extra_vars = _controller_refresh_extra_vars(ctx.hosts_path, target)
    if runtime_revision is not None and runtime_revision != resolved_runtime_revision:
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected controller runtime revision changed during bootstrap",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Re-run host bootstrap --plan and apply the newly selected controller revision",
            details={"host": target.uuid},
        )
    runtime_revision = resolved_runtime_revision
    control_root = _CONTROL_ROOT
    ssh_args = "-o BatchMode=yes -o ConnectTimeout=10 -o StrictHostKeyChecking=yes "
    try:
        with _controller_refresh_source(control_root, runtime_revision) as source:
            with _pinned_known_hosts(request) as known_hosts:
                completed = subprocess.run(
                    [
                        "ansible-playbook",
                        "-i",
                        f"{request.address},",
                        "-u",
                        "root",
                        "--ssh-common-args",
                        ssh_args + f"-o UserKnownHostsFile={known_hosts}",
                        str(source / _CONTROLLER_REFRESH_PLAYBOOK),
                        "-e",
                        json.dumps(extra_vars, sort_keys=True),
                    ],
                    cwd=source,
                    text=True,
                    capture_output=True,
                    check=False,
                )
    except (OSError, subprocess.TimeoutExpired):
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Declared host controller refresh is unavailable",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify the declared SSH transport and rerun host bootstrap --apply",
            details={"host": target.uuid, "runtime_revision": runtime_revision},
        ) from None
    if completed.returncode != 0:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Host controller refresh failed",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Inspect Bastion Ansible logs and rerun host bootstrap --apply",
            details={"host": target.uuid, "runtime_revision": runtime_revision},
        )


@contextmanager
def _controller_refresh_source(
    control_root: Path,
    revision: str | None,
    *,
    required_path: str | None = None,
    capability: str = "controller_refresh",
) -> Iterator[Path]:
    """Materialize an immutable management tree, never the live checkout."""
    required_path = required_path or _CONTROLLER_REFRESH_PLAYBOOK
    status = subprocess.run(
        ["git", "-C", str(control_root), "status", "--porcelain"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if status.returncode != 0 or status.stdout:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot materialize the selected immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Clean and refresh the Bastion infra-management checkout at the selected revision",
            details={"capability": capability, "required_revision": revision},
        )
    remote = subprocess.run(
        ["git", "-C", str(control_root), "remote", "get-url", "origin"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if remote.returncode != 0 or _controller_remote_identity(
        remote.stdout.strip()
    ) != _controller_remote_identity(_CONTROLLER_REFRESH_SOURCE_REMOTE):
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot fetch the selected immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Configure the expected infra-management origin and rerun host bootstrap --apply",
            details={"capability": capability, "required_revision": revision},
        )
    # `main` is transport only: the candidate-selected revision remains the
    # sole executable identity and must be reachable from the expected remote.
    fetched = subprocess.run(
        [
            "git",
            "-C",
            str(control_root),
            "fetch",
            "--no-tags",
            "--no-write-fetch-head",
            "origin",
            "refs/heads/main:refs/remotes/origin/main",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if revision is None and fetched.returncode == 0:
        resolved = subprocess.run(
            ["git", "-C", str(control_root), "rev-parse", "origin/main"],
            text=True,
            capture_output=True,
            check=False,
            env=_isolated_git_environment(),
        )
        revision = resolved.stdout.strip() if resolved.returncode == 0 else ""
    if not isinstance(revision, str) or re.fullmatch(r"[0-9a-f]{40}", revision) is None:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot resolve the immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify Bastion can read the expected infra-management origin and rerun host bootstrap --apply",
            details={"capability": capability},
        )
    present = subprocess.run(
        ["git", "-C", str(control_root), "cat-file", "-e", f"{revision}^{{commit}}"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    selected_from_main = subprocess.run(
        ["git", "-C", str(control_root), "merge-base", "--is-ancestor", revision, "origin/main"],
        text=True,
        capture_output=True,
        check=False,
        env=_isolated_git_environment(),
    )
    if fetched.returncode != 0 or present.returncode != 0 or selected_from_main.returncode != 0:
        raise CliFailure(
            code=ErrorCode.PROVIDER_UNAVAILABLE,
            message="Bastion cannot fetch the selected immutable management source",
            exit_code=ExitCode.PROVIDER_ERROR,
            fix="Verify Bastion can read the expected infra-management origin and rerun host bootstrap --apply",
            details={"capability": capability, "required_revision": revision},
        )
    with tempfile.TemporaryDirectory(prefix="infralink-controller-refresh-") as temporary:
        source = Path(temporary) / "source"
        created = subprocess.run(
            [
                "git",
                "-C",
                str(control_root),
                "worktree",
                "add",
                "--detach",
                str(source),
                revision,
            ],
            text=True,
            capture_output=True,
            check=False,
            env=_isolated_git_environment(),
        )
        head = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            text=True,
            capture_output=True,
            check=False,
            env=_isolated_git_environment(),
        )
        required = source / required_path
        if (
            created.returncode != 0
            or head.returncode != 0
            or head.stdout.strip() != revision
            or not required.is_file()
        ):
            raise CliFailure(
                code=ErrorCode.PROVIDER_UNAVAILABLE,
                message="Bastion could not materialize the selected immutable management source",
                exit_code=ExitCode.PROVIDER_ERROR,
                fix="Refresh the Bastion infra-management clone with the selected controller revision",
                details={"capability": capability, "required_revision": revision},
            )
        completed = False
        try:
            yield source
            completed = True
        finally:
            removed = subprocess.run(
                ["git", "-C", str(control_root), "worktree", "remove", "--force", str(source)],
                text=True,
                capture_output=True,
                check=False,
                env=_isolated_git_environment(),
            )
            if completed and removed.returncode != 0:
                raise CliFailure(
                    code=ErrorCode.ARTIFACT_IO_FAILED,
                    message="Bastion could not remove the temporary management source",
                    exit_code=ExitCode.ARTIFACT_IO_ERROR,
                    fix="Remove the temporary controller worktree and rerun host bootstrap --apply",
                    details={"capability": capability, "required_revision": revision},
                )


def _controller_refresh_extra_vars(registry_path: Path, target: Any) -> tuple[str, dict[str, Any]]:
    """Read the host controller revision, falling back to the fleet lock."""
    manifest_path = registry_path / target.uuid / "manifest.yml"
    try:
        document = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        declaration = document["hosts"][target.uuid]
    except (OSError, KeyError, TypeError, yaml.YAMLError):
        declaration = None
    deployment_path = registry_path / target.uuid / "operations" / "deployment.yml"
    if os.path.lexists(deployment_path):
        if deployment_path.is_file():
            try:
                deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
                runtime_revision = deployment["infra_management"]["revision"]
            except (OSError, KeyError, TypeError, yaml.YAMLError):
                runtime_revision = ""
        else:
            runtime_revision = ""
        revision_source = deployment_path
    else:
        lock = registry_path.parent / "operations" / "infra-management.lock"
        try:
            runtime_revision = lock.read_text(encoding="utf-8").strip()
        except OSError:
            runtime_revision = ""
        revision_source = lock
    if (
        not isinstance(runtime_revision, str)
        or re.fullmatch(r"[0-9a-f]{40}", runtime_revision) is None
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host does not bind an exact controller runtime revision",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare infra_management.revision in the host deployment or publish a valid fleet fallback lock",
            details={"path": str(revision_source)},
        )
    required_true = (
        "self_deploy_v2_reconcile_enabled",
        "self_deploy_v2_reconcile_packaged",
        "self_deploy_v2_promotion_policy_enabled",
    )
    required_strings = (
        "self_deploy_v2_promotion_registry_remote",
        "self_deploy_v2_promotion_bws_project_id",
        "self_deploy_v2_registry_read_identity_secret_uuid",
        "self_deploy_v2_promotion_host_fingerprint",
        "self_deploy_v2_promotion_allowed_signers",
        "self_deploy_v2_promotion_channel",
        "self_deploy_registry_origin",
    )
    if (
        not isinstance(declaration, dict)
        or any(declaration.get(name) is not True for name in required_true)
        or declaration.get("self_deploy_legacy_cron_enabled") is not False
        or any(
            not isinstance(declaration.get(name), str) or not declaration[name]
            for name in required_strings
        )
    ):
        raise CliFailure(
            code=ErrorCode.CONFIGURATION_REQUIRED,
            message="Selected host declaration is incomplete for controller refresh",
            exit_code=ExitCode.INPUT_ERROR,
            fix="Declare the complete V2 controller inputs in the selected host manifest",
            details={"host": target.uuid},
        )
    return runtime_revision, {
        "uuid": target.uuid,
        "canonical_name": target.canonical_name,
        "self_deploy_v2_runtime_revision": runtime_revision,
        "self_deploy_v2_reconcile_enabled": True,
        "self_deploy_v2_reconcile_packaged": True,
        "self_deploy_v2_promotion_policy_enabled": True,
        "self_deploy_legacy_cron_enabled": False,
        **{name: declaration[name] for name in required_strings},
    }
