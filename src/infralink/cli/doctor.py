"""Read-only declaration and observer-provenance diagnostics."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

import click
import yaml

from infralink.cli.actions import action
from infralink.cli.contracts import DoctorCoverage, DoctorEvidence, DoctorResult, DoctorTarget
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.main import Context, _context_for, _emit, _root_source_argv, pass_context
from infralink.cli.output import ok_envelope

DoctorKind = Literal["host", "service", "edge", "profile"]


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
    return CliFailure(
        code=ErrorCode.CONFIGURATION_REQUIRED,
        message="Observation plan configuration is required",
        exit_code=ExitCode.USAGE_ERROR,
        fix="Provide --observation-plan",
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
            {"status": host.status.value, "services": sorted(host.service_names)},
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
    return DoctorTarget(type="service", id=target_ref), {"host_ids": hosts}, target_ref


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
            or _dependency_matches(item, target_type, target_id, profile_services)
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


def _emit_result(ctx: Context, result: DoctorResult, path: list[str], actions: list[Any]) -> None:
    _emit(ok_envelope(_context_for(path=path), result, actions))


@click.command(name="doctor")
@click.option("--observation-plan", type=click.Path(path_type=Path), default=None)
@click.option("--adapter-bindings", type=click.Path(path_type=Path), default=None)
@click.option(
    "--validate", "declaration_only", is_flag=True, help="Validate declarations without I/O"
)
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
    target_type: DoctorKind | None,
    target_ref: str | None,
) -> int:
    """Inspect declared topology and observer-provenance evidence."""
    if target_type is None:
        if target_ref is not None:
            raise click.UsageError("a target type is required")
        from infralink.cli.queries import list_services

        if observation_plan is None and declaration_only:
            raise _configuration_required(ctx, "observation_plan")
        plan = _load_mapping(observation_plan, "observation_plan") if observation_plan else None
        bindings = _load_mapping(adapter_bindings, "adapter_bindings") if adapter_bindings else None
        coverage, evidence = _coverage(plan, bindings, None, "") if plan is not None else (None, [])

        result = DoctorResult(
            target=DoctorTarget(type="global"),
            declared={
                "host_count": len(ctx.registry),
                "service_count": len(list_services(ctx.registry, ctx.edges).items),
                "edge_count": len(ctx.edges),
            },
            evidence=evidence,
            coverage=coverage,
            status="unknown",
            reason=(
                "observer_coverage_incomplete"
                if coverage is not None and not coverage.valid
                else "no_live_observation_evidence"
                if plan is not None
                else "no_observation_evidence"
            ),
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
        )
        return 0

    if target_ref is None:
        raise click.UsageError("a target reference is required")
    if observation_plan is None:
        if declaration_only or target_type == "profile":
            raise _configuration_required(ctx, "observation_plan")
        target, declared, _ = _target(ctx, target_type, target_ref, None)
        result = DoctorResult(
            target=target,
            declared=declared,
            evidence=[],
            status="unknown",
            reason="no_observation_evidence",
        )
        _emit_result(
            ctx,
            result,
            ["doctor", target_type],
            [
                action(
                    "help",
                    [*_root_source_argv(ctx), "help", "doctor"],
                    "Show observation input options",
                )
            ],
        )
        return 0

    plan = _load_mapping(observation_plan, "observation_plan")
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
    reason = (
        "observer_coverage_incomplete" if not coverage.valid else "no_live_observation_evidence"
    )
    result = DoctorResult(
        target=target,
        declared=declared,
        evidence=evidence,
        coverage=coverage,
        status="unknown",
        reason=reason,
    )
    actions = [
        *(
            [
                action(
                    "show",
                    [*_root_source_argv(ctx), target_type, "show", target_id],
                    f"Show {target_type}",
                )
            ]
            if target_type != "profile"
            else [
                action(
                    "help",
                    [*_root_source_argv(ctx), "help", "doctor"],
                    "Show doctor usage",
                )
            ]
        ),
        action(
            "doctor",
            [
                *_root_source_argv(ctx),
                "doctor",
                "--observation-plan",
                str(observation_plan),
                *(
                    ["--adapter-bindings", str(adapter_bindings)]
                    if adapter_bindings is not None
                    else []
                ),
                target_type,
                target_id,
                "--validate",
            ],
            "Validate declared observer coverage",
        ),
    ]
    _emit_result(ctx, result, ["doctor", target_type], actions)
    return 0
