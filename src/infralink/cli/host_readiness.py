"""CLI adapter for the shared, read-only host readiness evaluator."""

from __future__ import annotations

from typing import Any, Protocol

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
    readiness = HostReadinessEvaluator().evaluate(canonical_name=canonical_name, probe=probe)
    return HostReadinessResult(
        transport=transport_name,
        ready=readiness.ready,
        checks=[HostReadinessCheck(**check.__dict__) for check in readiness.checks],
        actions=[HostBootstrapAction(**action.__dict__) for action in readiness.actions],
    )
