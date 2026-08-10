"""CLI adapter for the shared, read-only host readiness evaluator."""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Literal, Protocol, cast

from infralink.cli.contracts import HostBootstrapAction, HostReadinessCheck, HostReadinessResult
from infralink.host_readiness import HostReadinessEvaluator, HostReadinessProbe


class ReadinessTransport(Protocol):
    def probe(self, address: str) -> HostReadinessProbe: ...


def evaluate_host_readiness(
    host: Any,
    transport: ReadinessTransport | None,
) -> HostReadinessResult:
    """Return one typed baseline evaluation; no command owns its own checks."""
    canonical_name = str(host.canonical_name)
    if transport is None:
        probe = HostReadinessProbe(
            reachable=False,
            hostname=None,
            machine_id=None,
            commands={},
            devops_account=False,
            devops_authorized_access=False,
            bws_config=False,
            self_deploy_runtime=False,
            self_deploy_timer_enabled=False,
            self_deploy_timer_active=False,
            error="readiness_not_probed",
        )
        transport_name = "declaration_only"
    else:
        address = getattr(host, "tailscale_ip", None) or getattr(host, "public_ip", None) or ""
        probe = transport.probe(str(address))
        transport_name = "root_ssh"
    requires_v2_registry_layout = bool(
        getattr(host, "self_deploy_v2_registry_layout_enabled", False)
    )
    require_reconcile = bool(getattr(host, "self_deploy_v2_reconcile_enabled", True))
    probe = replace(probe, requires_v2_registry_layout=requires_v2_registry_layout)
    readiness = HostReadinessEvaluator().evaluate(
        canonical_name=canonical_name,
        probe=probe,
        require_reconcile=require_reconcile,
    )
    return HostReadinessResult(
        transport=cast(Literal["root_ssh", "declaration_only"], transport_name),
        ready=readiness.ready,
        checks=[HostReadinessCheck(**check.__dict__) for check in readiness.checks],
        actions=[HostBootstrapAction(**action.__dict__) for action in readiness.actions],
        runtime_mode=(
            cast(Literal["legacy_pull", "v2_reconcile"], probe.self_deploy_mode)
            if probe.self_deploy_mode in {"legacy_pull", "v2_reconcile"}
            else None
        ),
        registry_layout=(
            cast(
                Literal["v2_managed", "legacy_nested", "missing", "unsafe"],
                probe.registry_layout,
            )
            if probe.registry_layout in {"v2_managed", "legacy_nested", "missing", "unsafe"}
            else "unsafe"
        ),
        requires_v2_registry_layout=requires_v2_registry_layout,
        self_deploy_reconcile_result=probe.self_deploy_reconcile_result,
        self_deploy_reconcile_exit_status=probe.self_deploy_reconcile_exit_status,
        self_deploy_reconcile_active_state=probe.self_deploy_reconcile_active_state,
        self_deploy_reconcile_sub_state=probe.self_deploy_reconcile_sub_state,
        self_deploy_reconcile_exit_timestamp_monotonic=(
            probe.self_deploy_reconcile_exit_timestamp_monotonic
        ),
    )
