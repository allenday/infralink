"""Read-only declaration and observer-provenance diagnostics."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from ipaddress import ip_network
from pathlib import Path
from typing import Any, Literal, NoReturn
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import click
import yaml

from infralink.cli.actions import action
from infralink.cli.contracts import (
    DoctorCoverage,
    DoctorEvidence,
    DoctorEvidenceSummary,
    DoctorResult,
    DoctorTarget,
    HostReadinessCheck,
    HostReadinessResult,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.host_readiness import evaluate_host_readiness
from infralink.cli.main import (
    Context,
    _context_for,
    _emit,
    _root_source_argv,
    pass_context,
    registry_companion,
)
from infralink.cli.output import ok_envelope
from infralink.host_registry_state import HostManifestGitState, inspect_host_manifest
from infralink.host_transport import SshReadinessTransport
from infralink.observation.loader import load_observation_documents
from infralink.observation.v2 import ObservationV2Document

DoctorKind = Literal["host", "service", "edge", "profile"]
OBSERVATION_PLAN_ENVVAR = "INFRALINK_OBSERVATION_PLAN"
ADAPTER_BINDINGS_ENVVAR = "INFRALINK_ADAPTER_BINDINGS"
GATUS_URL_ENVVAR = "INFRALINK_GATUS_URL"
GATUS_TOKEN_ENVVAR = "INFRALINK_GATUS_TOKEN"
V2_OBSERVATION_RELATIVE = "service-catalog/v2"


def gatus_display_names(observation_plan: Path, adapter_bindings: Path) -> dict[str, str]:
    """Read the rendered Gatus identity map beside selected observer inputs.

    Gatus exposes its human-readable endpoint name, while dependency evidence is
    keyed by the registry's stable check ID.  The rendered artifact is the sole
    projection that joins those identities; Doctor must not recreate that
    presentation logic.
    """
    plan_directory = observation_plan.resolve().parent
    bindings_directory = adapter_bindings.resolve().parent
    if plan_directory != bindings_directory:
        _gatus_identity_failure(
            "Observation plan and adapter bindings must be from the same directory",
            "Provide observer inputs from one registry checkout",
            {
                "source": "gatus_rendered_identities",
                "observation_plan": str(observation_plan),
                "adapter_bindings": str(adapter_bindings),
            },
        )
    directory = plan_directory / "rendered" / "gatus"
    if not directory.is_dir():
        _gatus_identity_failure(
            "Rendered Gatus identity artifacts could not be loaded",
            "Render the selected registry observation artifacts before running live Doctor",
            {"source": "gatus_rendered_identities", "path": str(directory)},
        )
    result: dict[str, str] = {}
    display_ids: dict[str, str] = {}
    for path in sorted((*directory.glob("*.yml"), *directory.glob("*.yaml"))):
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            _gatus_identity_failure(
                "Rendered Gatus identity artifacts could not be loaded",
                "Render valid Gatus artifacts from the selected registry checkout",
                {"source": "gatus_rendered_identities", "path": str(path)},
            )
        if (
            not isinstance(document, dict)
            or document.get("schema_version") != "infra-observe.gatus-fragment.v1"
        ):
            _gatus_identity_failure(
                "Rendered Gatus identity artifacts are invalid",
                "Render valid Gatus artifacts from the selected registry checkout",
                {"source": "gatus_rendered_identities", "path": str(path)},
            )
        checks = document.get("checks") if isinstance(document, dict) else None
        if not isinstance(checks, list):
            _gatus_identity_failure(
                "Rendered Gatus identity artifacts are invalid",
                "Render valid Gatus artifacts from the selected registry checkout",
                {"source": "gatus_rendered_identities", "path": str(path)},
            )
        for check in checks:
            if not isinstance(check, dict):
                _gatus_identity_failure(
                    "Rendered Gatus identity artifacts are invalid",
                    "Render valid Gatus artifacts from the selected registry checkout",
                    {"source": "gatus_rendered_identities", "path": str(path)},
                )
            check_id = check.get("id")
            display_name = check.get("display_name")
            if (
                not isinstance(check_id, str)
                or not check_id
                or not isinstance(display_name, str)
                or not display_name
            ):
                _gatus_identity_failure(
                    "Rendered Gatus identity artifacts are invalid",
                    "Render valid Gatus artifacts from the selected registry checkout",
                    {"source": "gatus_rendered_identities", "path": str(path)},
                )
            prior = result.get(check_id)
            if prior is not None and prior != display_name:
                _gatus_identity_failure(
                    "Rendered Gatus identity artifacts conflict",
                    "Resolve duplicate Gatus check IDs in the selected registry checkout",
                    {"source": "gatus_rendered_identities", "path": str(path)},
                )
            prior_id = display_ids.get(display_name)
            if prior_id is not None and prior_id != check_id:
                _gatus_identity_failure(
                    "Rendered Gatus identity artifacts conflict",
                    "Resolve duplicate Gatus display names in the selected registry checkout",
                    {"source": "gatus_rendered_identities", "path": str(path)},
                )
            result[check_id] = display_name
            display_ids[display_name] = check_id
    return result


def _gatus_identity_failure(message: str, fix: str, details: dict[str, str]) -> NoReturn:
    raise CliFailure(
        code=ErrorCode.INPUT_LOAD_FAILED,
        message=message,
        exit_code=ExitCode.INPUT_ERROR,
        fix=fix,
        details=details,
    )


def _fetch_gatus_statuses(url: str, token: str | None) -> list[dict[str, Any]]:
    request = Request(f"{url.rstrip('/')}/api/v1/endpoints/statuses")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urlopen(request, timeout=10) as response:  # noqa: S310 - configured operator endpoint
            value = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError):
        raise RuntimeError("gatus_result_api_unavailable") from None
    if not isinstance(value, list):
        raise RuntimeError("gatus_result_api_invalid")
    return [item for item in value if isinstance(item, dict)]


def _gatus_evidence(
    evidence: list[DoctorEvidence],
    bindings: dict[str, Any] | None,
    url: str | None,
    token: str | None,
    display_names: Mapping[str, str] | None = None,
) -> list[DoctorEvidence]:
    if not url:
        return evidence
    try:
        statuses = _fetch_gatus_statuses(url, token)
    except RuntimeError as error:
        return [
            item.model_copy(update={"status": "unavailable", "reason": str(error)})
            if item.adapter == "gatus" and item.reason == "no_live_observation_evidence"
            else item
            for item in evidence
        ]
    binding_by_identity = {
        item.get("output_identity"): item
        for item in (bindings or {}).get("bindings", [])
        if isinstance(item, dict) and item.get("renderer_kind") == "gatus"
    }
    by_identity = {item.get("name"): item for item in statuses if isinstance(item.get("name"), str)}
    updated: list[DoctorEvidence] = []
    for item in evidence:
        binding = binding_by_identity.get(item.id)
        display_name = display_names.get(item.id) if display_names is not None else None
        status = (
            by_identity.get(display_name)
            if display_name is not None
            else by_identity.get(binding.get("output_identity"))
            if isinstance(binding, dict)
            else None
        )
        if item.adapter != "gatus" or item.reason != "no_live_observation_evidence":
            updated.append(item)
        elif status is None:
            updated.append(
                item.model_copy(
                    update={
                        "status": "unavailable" if display_names is not None else "unknown",
                        "reason": (
                            "gatus_rendered_identity_missing"
                            if display_names is not None
                            else "gatus_output_identity_missing"
                        ),
                    }
                )
            )
        else:
            results = status.get("results")
            latest = (
                results[-1]
                if isinstance(results, list) and results and isinstance(results[-1], dict)
                else {}
            )
            success = latest.get("success")
            observed_at = (
                latest.get("timestamp") if isinstance(latest.get("timestamp"), str) else None
            )
            updated.append(
                item.model_copy(
                    update={
                        "status": "healthy"
                        if success is True
                        else "unhealthy"
                        if success is False
                        else "unknown",
                        "reason": None if isinstance(success, bool) else "gatus_result_missing",
                        "observed_at": observed_at,
                    }
                )
            )
    return updated


def _result_status(
    coverage: DoctorCoverage | None, evidence: list[DoctorEvidence], gatus_url: str | None
) -> tuple[Literal["healthy", "unhealthy", "unavailable", "unknown"], str | None]:
    if coverage is not None and not coverage.valid:
        return "unknown", "observer_coverage_incomplete"
    if gatus_url is None and any(item.adapter == "gatus" for item in evidence):
        return "unknown", "gatus_not_configured"
    statuses = {item.status for item in evidence}
    if "unavailable" in statuses:
        reasons = {item.reason for item in evidence if item.status == "unavailable"}
        return "unavailable", reasons.pop() if len(reasons) == 1 else "gatus_result_api_unavailable"
    if "unhealthy" in statuses:
        return "unhealthy", "gatus_observation_unhealthy"
    if "healthy" in statuses and statuses <= {"healthy"}:
        return "healthy", None
    return "unknown", "no_live_observation_evidence"


def _doctor_prefix(
    ctx: Context,
    observation_plan: Path | None = None,
    adapter_bindings: Path | None = None,
) -> list[str]:
    return [
        *_root_source_argv(ctx),
        "doctor",
        *(["--observation-plan", str(observation_plan)] if observation_plan else []),
        *(["--adapter-bindings", str(adapter_bindings)] if adapter_bindings else []),
    ]


def _verbose_doctor_prefix(
    ctx: Context,
    observation_plan: Path | None,
    adapter_bindings: Path | None,
) -> list[str]:
    prefix = _doctor_prefix(ctx, observation_plan, adapter_bindings)
    return [prefix[0], "--verbose", *prefix[1:]]


def _missing(
    ctx: Context,
    kind: DoctorKind,
    ref: str,
    observation_plan: Path | None = None,
    adapter_bindings: Path | None = None,
) -> CliFailure:
    collection = {"host": "host", "service": "service", "edge": "edge"}.get(kind)
    argv = (
        [*_root_source_argv(ctx), collection, "list"]
        if collection is not None
        else [*_root_source_argv(ctx), "help", "doctor"]
    )
    return CliFailure(
        code=ErrorCode.ENTITY_NOT_FOUND,
        message=f"{kind.title()} not found",
        exit_code=ExitCode.INPUT_ERROR,
        fix=(
            f"Run infralink {collection} list"
            if collection is not None
            else "Run infralink help doctor"
        ),
        details={"entity_type": kind, "requested_id": ref},
        next_actions=[
            action(
                "list",
                argv,
                (f"List {collection} records" if collection is not None else "Show doctor usage"),
            )
        ],
    )


def _configuration_required(ctx: Context, source: str) -> CliFailure:
    labels = {
        "observation_plan": "Observation plan",
        "adapter_bindings": "Adapter bindings",
        "gatus_url": "Gatus URL",
    }
    envvars = {
        "observation_plan": OBSERVATION_PLAN_ENVVAR,
        "adapter_bindings": ADAPTER_BINDINGS_ENVVAR,
        "gatus_url": GATUS_URL_ENVVAR,
    }
    return CliFailure(
        code=ErrorCode.CONFIGURATION_REQUIRED,
        message=f"{labels[source]} configuration is required",
        exit_code=ExitCode.USAGE_ERROR,
        fix=f"Provide --{source.replace('_', '-')} or set {envvars[source]}",
        details={"source": source},
        next_actions=[
            action("help", [*_root_source_argv(ctx), "help", "doctor"], "Show doctor usage"),
        ],
    )


def _load_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw) if path.suffix == ".json" else yaml.safe_load(raw)
    except (OSError, ValueError, yaml.YAMLError):
        raise CliFailure(
            code=ErrorCode.INPUT_LOAD_FAILED,
            message=f"{label.title()} could not be loaded",
            exit_code=ExitCode.INPUT_ERROR,
            fix=f"Provide a valid {label} input",
            details={"source": label, "path": str(path)},
        ) from None
    if not isinstance(value, dict):
        raise CliFailure(
            code=ErrorCode.INPUT_LOAD_FAILED,
            message=f"{label.title()} could not be loaded",
            exit_code=ExitCode.INPUT_ERROR,
            fix=f"Provide a mapping document for {label}",
            details={"source": label, "path": str(path)},
        )
    return value


def _dependency_matches(
    dependency: dict[str, Any], target_type: DoctorKind, target_id: str, profile_services: set[str]
) -> bool:
    if target_type == "edge":
        return dependency.get("id") == target_id
    if target_type == "host":
        return any(
            isinstance(value, str) and value.startswith(f"{target_id}/")
            for value in (
                dependency.get("source_service_id"),
                dependency.get("target_service_id"),
            )
        )
    if target_type == "profile":
        return any(
            isinstance(value, str) and value in profile_services
            for value in (
                dependency.get("source_service_id"),
                dependency.get("target_service_id"),
            )
        )
    return any(
        isinstance(value, str) and value.rsplit("/", 1)[-1] == target_id
        for value in (
            dependency.get("source_service_id"),
            dependency.get("target_service_id"),
        )
    )


def _target(
    ctx: Context,
    target_type: DoctorKind,
    target_ref: str,
    plan: dict[str, Any] | None,
    observation_plan: Path | None = None,
    adapter_bindings: Path | None = None,
) -> tuple[DoctorTarget, dict[str, Any], str]:
    if target_type == "host":
        host = ctx.registry.get(target_ref)
        if host is None:
            raise _missing(ctx, "host", target_ref, observation_plan, adapter_bindings)
        return (
            DoctorTarget(type="host", id=host.uuid, canonical_name=host.canonical_name),
            {"status": host.status.value, "service_count": len(host.service_names)},
            host.uuid,
        )
    if target_type == "edge":
        edge = ctx.edges.get(target_ref)
        dependency = next(
            (
                item
                for item in (plan or {}).get("dependencies", [])
                if isinstance(item, dict) and item.get("id") == target_ref
            ),
            None,
        )
        if edge is None:
            if dependency is None:
                raise _missing(ctx, "edge", target_ref, observation_plan, adapter_bindings)
            return (
                DoctorTarget(type="edge", id=target_ref),
                {
                    "target_service": dependency.get("target_service_id"),
                    "port": dependency.get("port"),
                    "protocol": dependency.get("protocol"),
                },
                target_ref,
            )
        return (
            DoctorTarget(type="edge", id=edge.id),
            {
                "target_host": edge.target_host,
                "target_service": edge.target_service,
                "port": edge.declared_target_port,
                "protocol": edge.protocol,
            },
            _observer_dependency_id(edge, plan) or edge.id,
        )
    if target_type == "profile":
        profiles = plan.get("service_profiles", []) if plan is not None else []
        if not any(isinstance(item, dict) and item.get("id") == target_ref for item in profiles):
            raise _missing(ctx, "profile", target_ref, observation_plan, adapter_bindings)
        return DoctorTarget(type="profile", id=target_ref), {"profile_id": target_ref}, target_ref

    logical_service = next(
        (
            item
            for item in (plan or {}).get("logical_services", [])
            if isinstance(item, dict) and item.get("id") == target_ref
        ),
        None,
    )
    if logical_service is not None:
        host_id = logical_service.get("host_id")
        component_service_ids = logical_service.get("component_service_ids")
        if not isinstance(host_id, str) or not isinstance(component_service_ids, list):
            raise _missing(ctx, "service", target_ref, observation_plan, adapter_bindings)
        components = [item for item in component_service_ids if isinstance(item, str)]
        if len(components) != len(component_service_ids):
            raise _missing(ctx, "service", target_ref, observation_plan, adapter_bindings)
        host = ctx.registry.get(host_id)
        aggregate_name = target_ref.rsplit("/", 1)[-1]
        return (
            DoctorTarget(
                type="service",
                id=target_ref,
                canonical_name=(
                    f"{host.canonical_name}/{aggregate_name}" if host is not None else None
                ),
            ),
            {
                "host_id": host_id,
                "component_service_ids": components,
                "component_count": len(components),
            },
            target_ref,
        )

    service_ids = {
        service_id
        for host in ctx.registry
        for service_id in set(host.service_names) | set(host.roles)
    }
    service_ids.update(edge.target_service for edge in ctx.edges)
    if target_ref not in service_ids:
        raise _missing(ctx, "service", target_ref, observation_plan, adapter_bindings)
    hosts = sorted(
        host.uuid
        for host in ctx.registry
        if target_ref in set(host.service_names) | set(host.roles)
    )
    return DoctorTarget(type="service", id=target_ref), {"host_count": len(hosts)}, target_ref


def _observer_dependency_id(edge: Any, plan: dict[str, Any] | None) -> str | None:
    """Map a topology edge to one unambiguous declared observer dependency."""
    if plan is None or edge.source_service is None:
        return None
    target = f"{edge.target_host}/{edge.target_service}"
    candidates = [
        item
        for item in plan.get("dependencies", [])
        if isinstance(item, dict)
        and item.get("target_service_id") == target
        and isinstance(item.get("source_service_id"), str)
        and item["source_service_id"].endswith(f"/{edge.source_service}")
        and (
            edge.is_wildcard_source()
            or any(item["source_service_id"].startswith(f"{host}/") for host in edge.source_hosts)
        )
        and isinstance(item.get("id"), str)
    ]
    return candidates[0]["id"] if len(candidates) == 1 else None


def _coverage(
    plan: dict[str, Any],
    bindings: dict[str, Any] | None,
    target_type: DoctorKind | None,
    target_id: str,
) -> tuple[DoctorCoverage, list[DoctorEvidence]]:
    logical_service = next(
        (
            item
            for item in plan.get("logical_services", [])
            if isinstance(item, dict) and item.get("id") == target_id
        ),
        None,
    )
    logical_service_components = (
        {
            component
            for component in logical_service.get("component_service_ids", [])
            if isinstance(component, str)
        }
        if logical_service is not None
        else None
    )
    logical_service_signal_refs = (
        {
            signal_ref
            for signal_ref in logical_service.get("health_signal_refs", [])
            if isinstance(signal_ref, str)
        }
        if logical_service is not None
        else set()
    )
    profile_services = {
        item["id"]
        for item in plan.get("services", [])
        if isinstance(item, dict)
        and item.get("profile_id") == target_id
        and isinstance(item.get("id"), str)
    }
    dependencies = [
        item
        for item in plan.get("dependencies", [])
        if isinstance(item, dict)
        and (
            target_type is None
            or (
                (
                    (item.get("source_service_id") in logical_service_components)
                    or (item.get("target_service_id") in logical_service_components)
                )
                if logical_service_components is not None
                else _dependency_matches(item, target_type, target_id, profile_services)
            )
        )
    ]
    binding_by_identity = {
        item.get("output_identity"): item
        for item in (bindings or {}).get("bindings", [])
        if isinstance(item, dict) and isinstance(item.get("output_identity"), str)
    }
    evidence: list[DoctorEvidence] = []
    required = bound = unbound = unsupported = 0
    for dependency in sorted(dependencies, key=lambda item: str(item.get("id", ""))):
        required_dependency = dependency.get("required") is True
        if required_dependency:
            required += 1
        identity = dependency.get("id")
        adapter = dependency.get("execution_adapter")
        signals = dependency.get("health_signal_refs", [])
        signal_refs = [value for value in signals if isinstance(value, str)]
        binding = binding_by_identity.get(identity)
        supported = (
            adapter == "gatus" and binding is not None and binding.get("renderer_kind") == "gatus"
        )
        if supported:
            if required_dependency:
                bound += 1
            reason = "no_live_observation_evidence"
        elif adapter == "edge-prober":
            if required_dependency:
                unsupported += 1
            reason = "observer_result_api_undeclared"
        else:
            if required_dependency:
                unbound += 1
            reason = "observer_binding_undeclared"
        evidence.append(
            DoctorEvidence(
                id=str(identity),
                adapter=adapter if isinstance(adapter, str) else None,
                signal_refs=signal_refs,
                status="unknown",
                reason=reason,
            )
        )
    observed_signal_refs = {
        signal_ref
        for dependency in dependencies
        for signal_ref in dependency.get("health_signal_refs", [])
        if isinstance(signal_ref, str)
    }
    for signal_ref in sorted(logical_service_signal_refs - observed_signal_refs):
        required += 1
        signal_bindings = [
            binding
            for binding in (bindings or {}).get("bindings", [])
            if isinstance(binding, dict)
            and binding.get("renderer_kind") == "gatus"
            and binding.get("signal_ref") == signal_ref
            and isinstance(binding.get("output_identity"), str)
        ]
        if len(signal_bindings) == 1:
            binding = signal_bindings[0]
            bound += 1
            evidence.append(
                DoctorEvidence(
                    id=binding["output_identity"],
                    adapter="gatus",
                    signal_refs=[signal_ref],
                    status="unknown",
                    reason="no_live_observation_evidence",
                )
            )
        else:
            unbound += 1
            evidence.append(
                DoctorEvidence(
                    id=f"logical-service/{target_id}/{signal_ref}",
                    adapter=None,
                    signal_refs=[signal_ref],
                    status="unknown",
                    reason="observer_binding_undeclared",
                )
            )
    return (
        DoctorCoverage(
            required=required,
            bound=bound,
            unbound=unbound,
            unsupported=unsupported,
            valid=unbound == 0 and unsupported == 0,
        ),
        evidence,
    )


def _display_evidence(ctx: Context, evidence: list[DoctorEvidence]) -> list[DoctorEvidence]:
    """Keep healthy and absent observer detail behind the explicit verbose action."""
    if ctx.verbose:
        return evidence
    return [
        item
        for item in evidence
        if item.status in {"unhealthy", "unavailable"}
        or (item.status == "unknown" and item.reason != "no_live_observation_evidence")
    ]


def _evidence_summary(
    evidence: list[DoctorEvidence], gatus_url: str | None
) -> list[DoctorEvidenceSummary]:
    grouped: dict[str, list[DoctorEvidence]] = {}
    for item in evidence:
        grouped.setdefault(item.adapter or "undeclared", []).append(item)
    return [
        DoctorEvidenceSummary(
            adapter=adapter,
            configured=adapter == "gatus" and gatus_url is not None,
            healthy=sum(item.status == "healthy" for item in items),
            unhealthy=sum(item.status == "unhealthy" for item in items),
            unavailable=sum(item.status == "unavailable" for item in items),
            unknown=sum(item.status == "unknown" for item in items),
            live_observation_count=sum(item.observed_at is not None for item in items),
            latest_observed_at=max(
                (item.observed_at for item in items if item.observed_at is not None), default=None
            ),
        )
        for adapter, items in sorted(grouped.items())
    ]


def _emit_result(
    ctx: Context,
    result: DoctorResult,
    path: list[str],
    actions: list[Any],
    observation_plan: Path | None = None,
    adapter_bindings: Path | None = None,
    gatus_url: str | None = None,
    gatus_token_env: str | None = None,
    v2_observation_source: Path | None = None,
) -> None:
    command = _context_for(path=path)
    if observation_plan is not None:
        command.resolved["observation_plan"] = str(observation_plan)
    if adapter_bindings is not None:
        command.resolved["adapter_bindings"] = str(adapter_bindings)
    if gatus_url is not None:
        command.resolved["gatus_url"] = gatus_url
    command.resolved["gatus_configured"] = gatus_url is not None
    if gatus_token_env is not None:
        command.resolved["gatus_token_env"] = gatus_token_env
    if v2_observation_source is not None:
        command.resolved["v2_observation_source"] = str(v2_observation_source)
    _emit(ok_envelope(command, result, actions))


def _host_readiness(ctx: Context, target_ref: str, declaration_only: bool) -> Any:
    if declaration_only:
        return None
    host = ctx.registry.get(target_ref)
    if host is None:
        return None
    fingerprint_check: HostReadinessCheck | None = None
    manifest_path = ctx.hosts_path / str(host.uuid) / "manifest.yml" if ctx.hosts_path else None
    try:
        manifest = (
            yaml.safe_load(manifest_path.read_text(encoding="utf-8")) if manifest_path else {}
        )
        declared_v2 = bool(
            manifest.get("hosts", {})
            .get(str(host.uuid), {})
            .get("self_deploy_v2_target_ssh_host_fingerprint")
        )
    except (OSError, TypeError, yaml.YAMLError):
        declared_v2 = False
    if declared_v2:
        try:
            from infralink.cli.operations import pinned_target_ssh_identity, resolve_apply_request

            if ctx.hosts_path is None:
                raise ValueError
            request = resolve_apply_request(ctx.hosts_path, host)
            with pinned_target_ssh_identity(request) as known_hosts:
                readiness = evaluate_host_readiness(
                    host,
                    SshReadinessTransport(
                        _declared_firewall_rules(ctx, str(host.uuid)), known_hosts
                    ),
                )
            fingerprint_check = HostReadinessCheck(
                id="ssh_host_fingerprint",
                required=True,
                passed=True,
                description="Declared SSH host key matches the live target.",
            )
        except (CliFailure, ValueError):
            return HostReadinessResult(
                transport="root_ssh",
                ready=False,
                checks=[
                    HostReadinessCheck(
                        id="ssh_host_fingerprint",
                        required=True,
                        passed=False,
                        description="Declared SSH host key matches the live target.",
                        detail="ssh_host_fingerprint_mismatch",
                    )
                ],
                actions=[],
            )
    if fingerprint_check is None:
        readiness = evaluate_host_readiness(
            host, SshReadinessTransport(_declared_firewall_rules(ctx, str(host.uuid)))
        )
    return (
        readiness
        if fingerprint_check is None
        else readiness.model_copy(
            update={"ready": readiness.ready, "checks": [*readiness.checks, fingerprint_check]}
        )
    )


def _declared_firewall_rules(ctx: Context, host_uuid: str) -> tuple[str, ...]:
    if ctx.hosts_path is None or not ctx.hosts_path.is_dir():
        return ()
    deployment_path = ctx.hosts_path / host_uuid / "operations" / "deployment.yml"
    try:
        deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ()
    firewall = deployment.get("firewall") if isinstance(deployment, dict) else None
    if not isinstance(firewall, dict):
        return ()
    rules: list[str] = []
    management = firewall.get("management_ssh")
    if isinstance(management, dict):
        rules.extend(_firewall_rule_lines(management, "tcp"))
    ingress = firewall.get("ingress")
    if isinstance(ingress, list):
        for entry in ingress:
            if isinstance(entry, dict) and entry.get("protocol") in {"tcp", "udp"}:
                rules.extend(_firewall_rule_lines(entry, str(entry["protocol"])))
    return tuple(rules)


def _firewall_rule_lines(entry: dict[str, Any], protocol: str) -> list[str]:
    interface, sources = entry.get("interface"), entry.get("sources")
    ports = entry.get("ports", [entry.get("port")])
    if (
        not isinstance(interface, str)
        or not isinstance(sources, list)
        or not isinstance(ports, list)
    ):
        return []
    return [
        f'iifname "{interface}" {_nft_address_family(source)} saddr {_nft_source(source)} {protocol} dport {port} accept'
        for source in sources
        for port in ports
        if isinstance(source, str) and isinstance(port, int)
    ]


def _nft_source(source: str) -> str:
    """Match nft's canonical display, which suppresses an IPv4 /32 suffix."""
    try:
        network = ip_network(source, strict=True)
    except ValueError:
        return source
    return (
        str(network.network_address) if network.prefixlen == network.max_prefixlen else str(network)
    )


def _nft_address_family(source: str) -> str:
    try:
        return "ip6" if ip_network(source, strict=True).version == 6 else "ip"
    except ValueError:
        return "ip"


def _apply_host_readiness(result: DoctorResult, readiness: Any) -> DoctorResult:
    if readiness is None:
        return result
    if (
        result.declared.get("status") == "provisioning"
        and result.declared.get("service_count") == 0
        and readiness.ready
    ):
        return result.model_copy(
            update={
                "readiness": readiness,
                "status": "provisioning",
                "reason": "host_provisioning_ready",
            }
        )
    if not readiness.ready:
        if (
            result.declared.get("status") == "provisioning"
            and result.declared.get("service_count") == 0
        ):
            return result.model_copy(
                update={
                    "readiness": readiness,
                    "status": "provisioning",
                    "reason": "host_provisioning_incomplete",
                }
            )
        if result.status in {"unhealthy", "unavailable"}:
            return result.model_copy(update={"readiness": readiness})
        return result.model_copy(
            update={
                "readiness": readiness,
                "status": "unhealthy",
                "reason": "host_readiness_incomplete",
            }
        )
    return result.model_copy(update={"readiness": readiness})


def _host_manifest_git_state(ctx: Context, host_id: str) -> HostManifestGitState | None:
    if ctx.hosts_path is None or not ctx.hosts_path.is_dir():
        return None
    return inspect_host_manifest(ctx.hosts_path, host_id)


def _apply_host_manifest_git_state(
    result: DoctorResult, state: HostManifestGitState | None
) -> DoctorResult:
    if state is None:
        return result
    declared = {
        **result.declared,
        "registry_manifest": {
            "state": state.state,
            "manifest_path": str(state.manifest_path),
            "git_worktree": str(state.git_worktree),
        },
    }
    if state.state != "local_uncommitted":
        return result.model_copy(update={"declared": declared})
    lifecycle = result.declared.get("status")
    service_count = result.declared.get("service_count")
    status = "provisioning" if lifecycle == "provisioning" and service_count == 0 else "unhealthy"
    return result.model_copy(
        update={"declared": declared, "status": status, "reason": state.reason}
    )


def _nested_value(value: Any, *path: str) -> Any:
    for part in path:
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _host_deployment_contract(ctx: Context, host: Any) -> dict[str, Any] | None:
    """Inspect the one registry-owned deployment contract for an active host."""
    if ctx.hosts_path is None or not ctx.hosts_path.is_dir() or host.status.value != "active":
        return None
    deployment_path = ctx.hosts_path / host.uuid / "operations" / "deployment.yml"
    try:
        deployment = yaml.safe_load(deployment_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        deployment = None
    schema_version = deployment.get("schema_version") if isinstance(deployment, dict) else None
    if schema_version == "self-deploy.desired-state.v1":
        bootstrap = host.controller_bootstrap
        required = {
            "machine.uuid": _nested_value(deployment, "machine", "uuid"),
            "controller.image.repository": _nested_value(
                deployment, "controller", "image", "repository"
            ),
            "controller.image.tag": _nested_value(deployment, "controller", "image", "tag"),
            "infra_management.revision": _nested_value(deployment, "infra_management", "revision"),
            "compose.project_name": _nested_value(deployment, "compose", "project_name"),
            "images": deployment.get("images") if isinstance(deployment, dict) else None,
            "services.protected": _nested_value(deployment, "services", "protected"),
            "controller_bootstrap.registry_read_identity_secret.project": _nested_value(
                bootstrap, "registry_read_identity_secret", "project"
            ),
            "controller_bootstrap.registry_read_identity_secret.id": _nested_value(
                bootstrap, "registry_read_identity_secret", "id"
            ),
            "controller_bootstrap.registry_repo_url": _nested_value(bootstrap, "registry_repo_url"),
            "controller_bootstrap.registry_ref": _nested_value(bootstrap, "registry_ref"),
        }
        missing = [path for path, value in required.items() if not value]
        if not missing:
            try:
                from infralink.cli.operations import (
                    _normalize_manifest_fingerprint,
                    resolve_apply_request,
                )

                manifest_path = ctx.hosts_path / host.uuid / "manifest.yml"
                manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
                manifest_host = (
                    manifest.get("hosts", {}).get(host.uuid, {})
                    if isinstance(manifest, dict) and isinstance(manifest.get("hosts"), dict)
                    else {}
                )
                fingerprint = _normalize_manifest_fingerprint(
                    manifest_host.get("ssh", {}).get("host_key_fingerprint")
                    if isinstance(manifest_host, dict)
                    and isinstance(manifest_host.get("ssh"), dict)
                    else None
                )
                if fingerprint is None:
                    missing.append("ssh.host_key_fingerprint")
                else:
                    resolve_apply_request(ctx.hosts_path, host)
            except CliFailure:
                missing.append("controller_bootstrap.apply_contract")
            except (OSError, TypeError, yaml.YAMLError):
                missing.append("ssh.host_key_fingerprint")
        if not missing:
            return {"status": "ready", "schema_version": schema_version}
        return {
            "status": "incomplete",
            "code": "desired_state_contract_incomplete",
            "schema_version": schema_version,
            "missing": missing,
        }
    return {
        "status": "missing",
        "code": "desired_state_contract_missing",
        **({"schema_version": schema_version} if isinstance(schema_version, str) else {}),
    }


def _apply_host_deployment_contract(
    result: DoctorResult, contract: dict[str, Any] | None
) -> DoctorResult:
    if contract is None:
        return result
    declared = {**result.declared, "deployment_contract": contract}
    if contract["status"] == "ready":
        return result.model_copy(update={"declared": declared})
    return result.model_copy(
        update={
            "declared": declared,
            "status": "unhealthy",
            "reason": contract["code"],
        }
    )


def _host_v2_observation_contract(source: Path | None, host_id: str) -> dict[str, Any]:
    """Compile one host's V2 declaration without consulting runtime state."""
    if source is None:
        return {"status": "absent"}
    report = load_observation_documents(source)
    if not report.valid:
        return {
            "status": "invalid",
            "diagnostics": [
                {
                    "code": item.code,
                    "severity": item.severity,
                    "location": item.location.render(),
                    "message": item.message,
                }
                for item in report.diagnostics
            ],
        }
    non_v2_documents = [
        document
        for document in report.documents
        if document.schema_version != "infralink.observation/v2"
    ]
    if non_v2_documents:
        return {
            "status": "invalid",
            "diagnostics": [
                {
                    "code": "v2-observation-source-version-invalid",
                    "severity": "error",
                    "location": f"{document.source_path}#document={document.document_index}/schema_version",
                    "message": "The V2 observation catalog contains a non-V2 document.",
                }
                for document in non_v2_documents
            ],
        }
    documents = [
        ObservationV2Document.model_validate_json(json.dumps(document.to_dict()))
        for document in report.documents
        if document.schema_version == "infralink.observation/v2"
    ]
    profiles = {
        profile.id: profile for document in documents for profile in document.service_profiles
    }
    instances = [
        instance
        for document in documents
        for instance in document.service_instances
        if instance.host_id == host_id
    ]
    component_count = endpoint_count = endpoint_binding_count = resource_binding_count = 0
    metric_contract_count = 0
    for instance in instances:
        profile = profiles[instance.profile_id]
        slots = {slot.id: slot for slot in profile.components}
        for component in instance.components:
            slot = slots[component.slot_id]
            component_count += 1
            endpoint_count += len(slot.endpoints)
            endpoint_binding_count += len(component.endpoint_bindings)
            resource_binding_count += len(component.resource_bindings)
            metric_contract_count += len(slot.metrics)
    return {
        "status": "valid",
        "service_instance_count": len(instances),
        "component_count": component_count,
        "endpoint_count": endpoint_count,
        "endpoint_binding_count": endpoint_binding_count,
        "resource_binding_count": resource_binding_count,
        "metric_contract_count": metric_contract_count,
    }


def _apply_host_v2_observation_contract(
    result: DoctorResult, contract: dict[str, Any]
) -> DoctorResult:
    declared = {**result.declared, "v2_observation": contract}
    if contract["status"] != "invalid":
        return result.model_copy(update={"declared": declared})
    return result.model_copy(
        update={
            "declared": declared,
            "status": "unhealthy",
            "reason": "v2_observation_contract_invalid",
        }
    )


def _bootstrap_plan_action(ctx: Context, host_id: str) -> Any:
    return action(
        "bootstrap-plan",
        [*_root_source_argv(ctx), "host", "bootstrap", host_id, "--plan"],
        "Plan the failed host bootstrap prerequisites",
    )


def _verifier_action(ctx: Context, host_id: str) -> Any:
    return action(
        "verifier",
        [*_root_source_argv(ctx), "host", "verifier", host_id],
        "Inspect the read-only self-deploy verifier facts",
    )


@click.command(name="doctor")
@click.option(
    "--observation-plan",
    type=click.Path(path_type=Path),
    default=None,
    envvar=OBSERVATION_PLAN_ENVVAR,
)
@click.option(
    "--adapter-bindings",
    type=click.Path(path_type=Path),
    default=None,
    envvar=ADAPTER_BINDINGS_ENVVAR,
)
@click.option(
    "--validate", "declaration_only", is_flag=True, help="Validate declarations without I/O"
)
@click.option("--gatus-url", default=None, envvar=GATUS_URL_ENVVAR)
@click.option("--gatus-token-env", default=GATUS_TOKEN_ENVVAR)
@click.argument(
    "target_type", required=False, type=click.Choice(["host", "service", "edge", "profile"])
)
@click.argument("target_ref", required=False)
@pass_context
def doctor(
    ctx: Context,
    observation_plan: Path | None,
    adapter_bindings: Path | None,
    declaration_only: bool,
    gatus_url: str | None,
    gatus_token_env: str,
    target_type: DoctorKind | None,
    target_ref: str | None,
) -> int:
    """Inspect observer evidence; inputs accept INFRALINK_OBSERVATION_PLAN and INFRALINK_ADAPTER_BINDINGS."""
    observation_plan = observation_plan or registry_companion(
        ctx.registry_path, "operations/observation/core-plan.json"
    )
    adapter_bindings = adapter_bindings or registry_companion(
        ctx.registry_path, "operations/observation/adapter-bindings.yml"
    )
    v2_observation_source = registry_companion(ctx.registry_path, V2_OBSERVATION_RELATIVE)
    if target_type is None:
        if target_ref is not None:
            raise click.UsageError("a target type is required")
        from infralink.cli.queries import list_services

        if observation_plan is None:
            raise _configuration_required(ctx, "observation_plan")
        if adapter_bindings is None:
            raise _configuration_required(ctx, "adapter_bindings")
        plan = _load_mapping(observation_plan, "observation_plan") if observation_plan else None
        bindings = _load_mapping(adapter_bindings, "adapter_bindings") if adapter_bindings else None
        coverage, evidence = _coverage(plan, bindings, None, "") if plan is not None else (None, [])
        if (
            not declaration_only
            and gatus_url is None
            and any(item.adapter == "gatus" for item in evidence)
        ):
            raise _configuration_required(ctx, "gatus_url")
        if not declaration_only:
            assert observation_plan is not None
            assert adapter_bindings is not None
            display_names = (
                gatus_display_names(observation_plan, adapter_bindings)
                if any(item.adapter == "gatus" for item in evidence)
                else None
            )
            evidence = _gatus_evidence(
                evidence,
                bindings,
                gatus_url,
                os.environ.get(gatus_token_env),
                display_names,
            )
        status, reason = _result_status(coverage, evidence, gatus_url)

        result = DoctorResult(
            target=DoctorTarget(type="global"),
            declared={
                "host_count": len(ctx.registry),
                "service_count": len(list_services(ctx.registry, ctx.edges).items),
                "edge_count": len(ctx.edges),
            },
            evidence=_display_evidence(ctx, evidence),
            evidence_summary=_evidence_summary(evidence, gatus_url),
            coverage=coverage,
            status=status if plan is not None else "unknown",
            reason=reason if plan is not None else "no_observation_evidence",
        )
        _emit_result(
            ctx,
            result,
            ["doctor"],
            [
                action(
                    "help",
                    [*_root_source_argv(ctx), "help", "doctor"],
                    "Show doctor usage",
                ),
                action("list", [*_root_source_argv(ctx), "host", "list"], "List hosts"),
            ],
            observation_plan,
            adapter_bindings,
        )
        return (
            0
            if status == "healthy" or declaration_only and coverage is not None and coverage.valid
            else 1
        )

    if target_ref is None:
        raise click.UsageError("a target reference is required")
    static_v2_host_validation = (
        declaration_only and target_type == "host" and v2_observation_source is not None
    )
    if observation_plan is None and not static_v2_host_validation:
        raise _configuration_required(ctx, "observation_plan")
    if adapter_bindings is None and not static_v2_host_validation:
        raise _configuration_required(ctx, "adapter_bindings")

    plan = _load_mapping(observation_plan, "observation_plan") if observation_plan else {}
    bindings = _load_mapping(adapter_bindings, "adapter_bindings") if adapter_bindings else None
    target, declared, target_id = _target(
        ctx,
        target_type,
        target_ref,
        plan,
        observation_plan,
        adapter_bindings,
    )
    coverage, evidence = _coverage(plan, bindings, target_type, target_id)
    if (
        not declaration_only
        and gatus_url is None
        and any(item.adapter == "gatus" for item in evidence)
    ):
        raise _configuration_required(ctx, "gatus_url")
    if not declaration_only:
        assert observation_plan is not None
        assert adapter_bindings is not None
        display_names = (
            gatus_display_names(observation_plan, adapter_bindings)
            if any(item.adapter == "gatus" for item in evidence)
            else None
        )
        evidence = _gatus_evidence(
            evidence,
            bindings,
            gatus_url,
            os.environ.get(gatus_token_env),
            display_names,
        )
    status, reason = _result_status(coverage, evidence, gatus_url)
    result = DoctorResult(
        target=target,
        declared=declared,
        evidence=_display_evidence(ctx, evidence),
        evidence_summary=_evidence_summary(evidence, gatus_url),
        coverage=coverage,
        status=status,
        reason=reason,
    )
    actions = [
        action(
            "verbose",
            [
                *_verbose_doctor_prefix(ctx, observation_plan, adapter_bindings),
                *(["--gatus-url", gatus_url] if gatus_url else []),
                *(["--gatus-token-env", gatus_token_env] if gatus_url else []),
                target_type,
                target_id,
                *(["--validate"] if declaration_only else []),
            ],
            "Show complete declared observer evidence",
        ),
    ]
    if gatus_url is None and any(item.adapter == "gatus" for item in evidence):
        actions.append(
            action(
                "configure-gatus",
                [*_root_source_argv(ctx), "help", "doctor"],
                "Set INFRALINK_GATUS_URL or pass --gatus-url",
            )
        )
    readiness = (
        _host_readiness(ctx, target_ref, declaration_only) if target_type == "host" else None
    )
    result = _apply_host_readiness(result, readiness)
    host = ctx.registry.get(target_ref) if target_type == "host" else None
    deployment_contract = _host_deployment_contract(ctx, host) if host is not None else None
    result = _apply_host_deployment_contract(result, deployment_contract)
    if target_type == "host":
        result = _apply_host_v2_observation_contract(
            result, _host_v2_observation_contract(v2_observation_source, target_id)
        )
    if deployment_contract is not None and deployment_contract["status"] != "ready":
        actions.append(
            action(
                "inspect-deployment-contract",
                [*_root_source_argv(ctx), "host", "show", target_id],
                "Inspect the host declaration before authoring a desired-state contract",
            )
        )
    if readiness is not None and not readiness.ready:
        actions.append(_bootstrap_plan_action(ctx, target_id))
        if any(item.id == "inspect_self_deploy_reconcile" for item in readiness.actions):
            actions.append(_verifier_action(ctx, target_id))
    manifest_state = _host_manifest_git_state(ctx, target_id) if target_type == "host" else None
    result = _apply_host_manifest_git_state(result, manifest_state)
    if manifest_state is not None and manifest_state.state == "local_uncommitted":
        actions.append(
            action(
                "git-status",
                ["git", "-C", str(manifest_state.git_worktree), "status", "--short"],
                "Inspect the uncommitted registry change",
            )
        )
    _emit_result(
        ctx,
        result,
        ["doctor", target_type],
        actions,
        observation_plan,
        adapter_bindings,
        gatus_url,
        gatus_token_env,
        v2_observation_source,
    )
    return (
        0
        if result.status == "healthy"
        or declaration_only
        and coverage.valid
        and result.status != "unhealthy"
        else 1
    )
