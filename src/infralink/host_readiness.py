"""Read-only host bootstrap readiness contract and evaluator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineRequirement:
    id: str
    description: str
    action_id: str
    action_description: str
    required: bool = True


@dataclass(frozen=True)
class HostReadinessProbe:
    reachable: bool
    hostname: str | None
    machine_id: str | None
    commands: dict[str, bool]
    devops_account: bool
    devops_authorized_access: bool
    bws_config: bool
    self_deploy_runtime: bool
    self_deploy_timer_enabled: bool
    self_deploy_timer_active: bool
    error: str | None
    self_deploy_mode: str | None = None
    self_deploy_dependencies: bool = False
    registry_layout: str | None = None
    requires_v2_registry_layout: bool = False
    requires_controller_reconcile: bool = False
    self_deploy_reconcile_result: str | None = None
    self_deploy_reconcile_exit_status: int | None = None
    self_deploy_reconcile_active_state: str | None = None
    self_deploy_reconcile_sub_state: str | None = None
    self_deploy_reconcile_exit_timestamp_monotonic: int | None = None
    firewall_rules_expected: int = 0
    firewall_rules_matched: int = 0
    firewall_observable: bool = True
    tailscale_ips: tuple[str, ...] = ()
    tailscale_running: bool = False
    tailscale_name: str | None = None
    controller_image: str | None = None
    controller_python_version: str | None = None


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    required: bool
    passed: bool
    description: str
    detail: str | None


@dataclass(frozen=True)
class BootstrapAction:
    id: str
    check_id: str
    description: str


@dataclass(frozen=True)
class HostReadiness:
    ready: bool
    checks: list[ReadinessCheck]
    actions: list[BootstrapAction]


BASELINE_REQUIREMENTS: tuple[BaselineRequirement, ...] = (
    BaselineRequirement(
        "registry_layout",
        "Registry checkout layout is safe and conforms to declared migration policy.",
        "migrate_v2_registry_layout",
        "Migrate the host registry checkout to the V2-owned root.",
    ),
    BaselineRequirement(
        "ssh_reachable",
        "Root SSH is reachable.",
        "establish_root_ssh",
        "Establish root SSH access.",
    ),
    BaselineRequirement(
        "host_identity",
        "Remote hostname matches the declared canonical name.",
        "correct_host_identity",
        "Correct the declared host identity or remote hostname.",
    ),
    BaselineRequirement(
        "machine_id",
        "Remote machine ID is present.",
        "initialize_machine_id",
        "Initialize a stable machine ID.",
    ),
    BaselineRequirement(
        "devops_account",
        "The devops account exists.",
        "create_devops_account",
        "Create the devops account.",
        required=False,
    ),
    BaselineRequirement(
        "devops_authorized_access",
        "The devops account has authorized SSH access.",
        "configure_devops_authorized_access",
        "Install authorized SSH access for devops.",
        required=False,
    ),
    BaselineRequirement("git", "Git CLI is installed.", "install_git", "Install Git."),
    BaselineRequirement("docker", "Docker CLI is installed.", "install_docker", "Install Docker."),
    BaselineRequirement(
        "tailscale", "Tailscale CLI is installed.", "install_tailscale", "Install Tailscale."
    ),
    BaselineRequirement("jq", "jq CLI is installed.", "install_jq", "Install jq."),
    BaselineRequirement(
        "bws_cli",
        "Bitwarden Secrets Manager CLI is installed.",
        "install_bws_cli",
        "Install the Bitwarden Secrets Manager CLI.",
    ),
    BaselineRequirement(
        "bws_config",
        "Bitwarden Secrets Manager configuration is present.",
        "configure_bws",
        "Configure Bitwarden Secrets Manager access.",
    ),
    BaselineRequirement(
        "self_deploy_dependencies",
        "Controller host interface is installed.",
        "install_self_deploy_dependencies",
        "Install controller host prerequisites.",
    ),
    BaselineRequirement(
        "self_deploy_runtime",
        "Self-deploy runtime is installed.",
        "install_self_deploy_runtime",
        "Install the self-deploy runtime.",
    ),
    BaselineRequirement(
        "self_deploy_timer",
        "Self-deploy timer is enabled and active.",
        "enable_self_deploy_timer",
        "Enable and start the self-deploy timer.",
    ),
    BaselineRequirement(
        "self_deploy_reconcile",
        "Latest controller reconciliation completed successfully.",
        "inspect_self_deploy_reconcile",
        "Inspect and repair the latest self-deploy reconciliation failure.",
    ),
    BaselineRequirement(
        "controller_python",
        "Resolved controller image embeds Python 3.12 or newer.",
        "refresh_controller_image",
        "Publish or pull a controller image with Python 3.12 or newer.",
    ),
)


class HostReadinessEvaluator:
    """Evaluate the explicit bootstrap baseline from one read-only probe."""

    def evaluate(
        self,
        *,
        canonical_name: str,
        probe: HostReadinessProbe,
        require_reconcile: bool = True,
    ) -> HostReadiness:
        reconcile_passed, reconcile_detail = _reconcile_outcome(probe)
        outcomes: dict[str, tuple[bool, str | None]] = {
            "registry_layout": (
                probe.reachable
                and (
                    probe.registry_layout == "v2_managed"
                    or (
                        probe.registry_layout == "legacy_nested"
                        and not probe.requires_v2_registry_layout
                    )
                ),
                None
                if probe.registry_layout == "v2_managed"
                or (
                    probe.registry_layout == "legacy_nested"
                    and not probe.requires_v2_registry_layout
                )
                else probe.registry_layout or "missing",
            ),
            "ssh_reachable": (probe.reachable, probe.error),
            "host_identity": (
                probe.reachable and probe.hostname == canonical_name,
                None if probe.hostname == canonical_name else "hostname_mismatch",
            ),
            "machine_id": (probe.reachable and bool(probe.machine_id), "machine_id_missing"),
            "devops_account": (probe.reachable and probe.devops_account, "devops_account_missing"),
            "devops_authorized_access": (
                probe.reachable and probe.devops_authorized_access,
                "devops_authorized_access_missing",
            ),
            "git": (probe.reachable and probe.commands.get("git", False), "git_missing"),
            "docker": (probe.reachable and probe.commands.get("docker", False), "docker_missing"),
            "tailscale": (
                probe.reachable and probe.commands.get("tailscale", False),
                "tailscale_missing",
            ),
            "jq": (probe.reachable and probe.commands.get("jq", False), "jq_missing"),
            "bws_cli": (probe.reachable and probe.commands.get("bws", False), "bws_cli_missing"),
            "bws_config": (probe.reachable and probe.bws_config, "bws_config_missing"),
            "self_deploy_dependencies": (
                probe.reachable and probe.self_deploy_dependencies,
                "self_deploy_dependencies_missing",
            ),
            "self_deploy_runtime": (
                probe.reachable
                and probe.self_deploy_runtime
                and (
                    probe.self_deploy_mode is None
                    or probe.self_deploy_mode in {"legacy_pull", "v2_reconcile"}
                ),
                "self_deploy_runtime_missing",
            ),
            "self_deploy_timer": (
                probe.reachable
                and probe.self_deploy_timer_enabled
                and probe.self_deploy_timer_active,
                "self_deploy_timer_inactive",
            ),
            "self_deploy_reconcile": (reconcile_passed, reconcile_detail),
            "controller_python": _controller_python_outcome(probe),
        }
        checks = [
            ReadinessCheck(
                id=requirement.id,
                required=requirement.required
                and (
                    require_reconcile
                    or requirement.id
                    not in {"self_deploy_timer", "self_deploy_reconcile", "controller_python"}
                ),
                passed=outcomes[requirement.id][0]
                if require_reconcile
                or requirement.id
                not in {"self_deploy_timer", "self_deploy_reconcile", "controller_python"}
                else True,
                description=requirement.description,
                detail=None
                if (
                    not require_reconcile
                    and requirement.id
                    in {"self_deploy_timer", "self_deploy_reconcile", "controller_python"}
                )
                or outcomes[requirement.id][0]
                else outcomes[requirement.id][1],
            )
            for requirement in BASELINE_REQUIREMENTS
        ]
        if probe.firewall_rules_expected:
            firewall_passed = (
                probe.reachable
                and probe.firewall_observable
                and probe.firewall_rules_matched == probe.firewall_rules_expected
            )
            checks.append(
                ReadinessCheck(
                    id="declared_firewall",
                    required=True,
                    passed=firewall_passed,
                    description="Declared firewall ingress is active.",
                    detail=None
                    if firewall_passed
                    else (
                        "firewall_unobservable"
                        if not probe.firewall_observable
                        else f"{probe.firewall_rules_matched}/{probe.firewall_rules_expected}_rules_matched"
                    ),
                )
            )
        actions = [
            BootstrapAction(
                id=requirement.action_id,
                check_id=requirement.id,
                description=requirement.action_description,
            )
            for requirement in BASELINE_REQUIREMENTS
            if next(check for check in checks if check.id == requirement.id).required
            and not outcomes[requirement.id][0]
        ]
        if (
            probe.firewall_rules_expected
            and not next(check for check in checks if check.id == "declared_firewall").passed
        ):
            actions.append(
                BootstrapAction(
                    id="reconcile_declared_firewall",
                    check_id="declared_firewall",
                    description="Run reconciliation to apply the declared firewall ingress.",
                )
            )
        return HostReadiness(
            ready=all(not check.required or check.passed for check in checks),
            checks=checks,
            actions=actions,
        )


def _controller_python_outcome(probe: HostReadinessProbe) -> tuple[bool, str | None]:
    """Validate only the controller image interpreter for controller-managed hosts."""
    if not probe.reachable:
        return False, probe.error or "ssh_unreachable"
    if not probe.requires_controller_reconcile:
        return True, None
    if not probe.controller_image:
        return False, "controller_image_unresolved"
    version = probe.controller_python_version
    if version is None:
        return False, "controller_python_unavailable"
    try:
        major, minor, *_ = (int(part) for part in version.split("."))
    except ValueError:
        return False, "controller_python_invalid"
    if (major, minor) < (3, 12):
        return False, f"controller_python_too_old:{version}"
    return True, None


def _reconcile_outcome(probe: HostReadinessProbe) -> tuple[bool, str | None]:
    """Require a successful latest run for V2 without penalizing legacy hosts twice."""
    if not probe.reachable:
        return False, "self_deploy_reconcile_unavailable"
    if (
        probe.self_deploy_mode != "v2_reconcile"
        and not probe.requires_v2_registry_layout
        and not probe.requires_controller_reconcile
    ):
        return True, None
    if probe.self_deploy_mode != "v2_reconcile":
        return False, "self_deploy_reconcile_missing"
    if (
        probe.self_deploy_reconcile_result == "success"
        and probe.self_deploy_reconcile_exit_status == 0
        and probe.self_deploy_reconcile_active_state == "inactive"
        and probe.self_deploy_reconcile_sub_state == "dead"
        and (probe.self_deploy_reconcile_exit_timestamp_monotonic or 0) > 0
    ):
        return True, None
    if (
        probe.self_deploy_reconcile_result == "success"
        and probe.self_deploy_reconcile_exit_status == 0
    ):
        return False, "self_deploy_reconcile_not_completed"
    if probe.self_deploy_reconcile_result:
        suffix = (
            f":{probe.self_deploy_reconcile_exit_status}"
            if probe.self_deploy_reconcile_exit_status is not None
            else ""
        )
        return False, f"{probe.self_deploy_reconcile_result}{suffix}"
    return False, "self_deploy_reconcile_result_unknown"
