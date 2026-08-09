"""Read-only host bootstrap readiness contract and evaluator."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaselineRequirement:
    id: str
    description: str
    action_id: str
    action_description: str


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
    self_deploy_reconcile_result: str | None = None
    self_deploy_reconcile_exit_status: int | None = None
    self_deploy_reconcile_active_state: str | None = None
    self_deploy_reconcile_sub_state: str | None = None
    self_deploy_reconcile_exit_timestamp_monotonic: int | None = None


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
    ),
    BaselineRequirement(
        "devops_authorized_access",
        "The devops account has authorized SSH access.",
        "configure_devops_authorized_access",
        "Install authorized SSH access for devops.",
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
        "Self-deploy Python dependencies are installed.",
        "install_self_deploy_dependencies",
        "Install the self-deploy Python dependencies.",
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
        "Latest V2 self-deploy reconciliation completed successfully.",
        "inspect_self_deploy_reconcile",
        "Inspect and repair the latest self-deploy reconciliation failure.",
    ),
)


class HostReadinessEvaluator:
    """Evaluate the explicit bootstrap baseline from one read-only probe."""

    def evaluate(self, *, canonical_name: str, probe: HostReadinessProbe) -> HostReadiness:
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
        }
        checks = [
            ReadinessCheck(
                id=requirement.id,
                required=True,
                passed=outcomes[requirement.id][0],
                description=requirement.description,
                detail=None if outcomes[requirement.id][0] else outcomes[requirement.id][1],
            )
            for requirement in BASELINE_REQUIREMENTS
        ]
        actions = [
            BootstrapAction(
                id=requirement.action_id,
                check_id=requirement.id,
                description=requirement.action_description,
            )
            for requirement in BASELINE_REQUIREMENTS
            if not outcomes[requirement.id][0]
        ]
        return HostReadiness(
            ready=all(check.passed for check in checks), checks=checks, actions=actions
        )


def _reconcile_outcome(probe: HostReadinessProbe) -> tuple[bool, str | None]:
    """Require a successful latest run for V2 without penalizing legacy hosts twice."""
    if not probe.reachable:
        return False, "self_deploy_reconcile_unavailable"
    if probe.self_deploy_mode != "v2_reconcile":
        return True, None
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
