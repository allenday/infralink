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
)


class HostReadinessEvaluator:
    """Evaluate the explicit bootstrap baseline from one read-only probe."""

    def evaluate(self, *, canonical_name: str, probe: HostReadinessProbe) -> HostReadiness:
        outcomes: dict[str, tuple[bool, str | None]] = {
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
            "self_deploy_runtime": (
                probe.reachable and probe.self_deploy_runtime,
                "self_deploy_runtime_missing",
            ),
            "self_deploy_timer": (
                probe.reachable
                and probe.self_deploy_timer_enabled
                and probe.self_deploy_timer_active,
                "self_deploy_timer_inactive",
            ),
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
