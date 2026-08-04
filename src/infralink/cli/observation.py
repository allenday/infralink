"""Offline observation commands and serialization."""

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

import click
import yaml

from infralink.cli.actions import action
from infralink.cli.contracts import Action, Binding
from infralink.cli.observation_contracts import (
    CapabilitiesResult,
    DiagnosticSetResult,
    ExplainResult,
    ObservationCommand,
    ObservationEnvelope,
    ObservationError,
    ObservationMeta,
    ObservationPlan,
    ObservationReadinessSuite,
    ObservationValidateResult,
    ProjectObservationResult,
    ProjectReadinessResult,
    ProjectSecretsResult,
    ProjectViewResult,
    SourceProvenanceResult,
)
from infralink.cli.output import redact_argv


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _request_id() -> str:
    return str(uuid4())


def _parse_as_of(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise click.BadParameter("must be an RFC3339 timestamp", param_hint="--as-of") from None
    if parsed.tzinfo is None:
        raise click.BadParameter("must include a timezone", param_hint="--as-of")
    return parsed


def _format(ctx: Any) -> str:
    return "json" if getattr(ctx, "output_explicit", False) and ctx.output == "json" else "yaml"


def _command(path: list[str], values: dict[str, Any]) -> ObservationCommand:
    argv = [*path]
    for key, value in values.items():
        if value is not None:
            argv.extend([f"--{key.replace('_', '-')}", str(value)])
    return ObservationCommand(
        raw_redacted=shlex.join(argv), parsed={"path": path, "args": values}, resolved=values
    )


def _emit(ctx: Any, envelope: ObservationEnvelope[Any]) -> None:
    from infralink.cli.main import _ENVELOPE_EMITTED

    payload = envelope.model_dump(mode="json")
    _ENVELOPE_EMITTED.set(True)
    if _format(ctx) == "json":
        import json

        click.echo(json.dumps(payload, separators=(",", ":")))
    else:
        click.echo(yaml.safe_dump(payload, sort_keys=False), nl=False)


def emit_boundary_failure(incoming: list[str], *, code: str, message: str) -> None:
    """Emit an agent envelope when failure happens outside a command callback."""
    from infralink.cli.main import _ENVELOPE_EMITTED

    output = _output_from_argv(incoming)
    payload = {
        "schema_version": "agent-cli.response.v1",
        "request_id": str(uuid4()),
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "ok": False,
        "command": {
            "raw_redacted": shlex.join(redact_argv(incoming)),
            "parsed": {"path": [], "args": {}},
            "resolved": {},
        },
        "error": {"code": code, "message": message, "details": {}},
        "next_actions": [],
        "meta": {"truncated": False},
    }
    _ENVELOPE_EMITTED.set(True)
    if output == "json":
        import json

        click.echo(json.dumps(payload, separators=(",", ":")))
    else:
        click.echo(yaml.safe_dump(payload, sort_keys=False), nl=False)


def _output_from_argv(argv: list[str]) -> str:
    """Resolve Click's root output option forms without re-entering command parsing."""
    output = "yaml"
    index = 0
    while index < len(argv):
        token = argv[index]
        if token == "--":
            break
        if not token.startswith("-"):
            break
        if token in {"--registry", "--edges", "-r", "-e"}:
            index += 2
            continue
        if token.startswith("--registry=") or token.startswith("--edges="):
            index += 1
            continue
        if token == "--output" or token == "-o":
            if index + 1 < len(argv):
                output = argv[index + 1].lower()
            index += 2
            continue
        if token.startswith("--output="):
            output = token.partition("=")[2].lower()
            index += 1
            continue
        if token.startswith("-") and not token.startswith("--"):
            option_cluster = token[1:]
            output_offset = option_cluster.find("o")
            if output_offset >= 0:
                attached = option_cluster[output_offset + 1 :]
                if attached:
                    output = attached.lower()
                    index += 1
                elif index + 1 < len(argv):
                    output = argv[index + 1].lower()
                    index += 2
                else:
                    index += 1
                continue
        index += 1
    return output if output in {"json", "yaml"} else "yaml"


def is_observation_argv(argv: list[str]) -> bool:
    """Classify a root invocation using the option spellings accepted by Click."""
    index = 0
    while index < len(argv):
        token = argv[index]
        if token in {"--registry", "--edges", "--output", "-r", "-e", "-o"}:
            index += 2
            continue
        if token.startswith(("--registry=", "--edges=", "--output=")):
            index += 1
            continue
        if token.startswith("-") and not token.startswith("--"):
            cluster = token[1:]
            value_option = next(
                (offset for offset, char in enumerate(cluster) if char in "reo"), None
            )
            if value_option is not None and value_option == len(cluster) - 1:
                index += 2
            else:
                index += 1
            continue
        break
    if index >= len(argv):
        return False
    command = argv[index]
    if command in {"capabilities", "project", "explain"}:
        return True
    return command == "validate" and any(
        token == "--source" or token.startswith("--source=") for token in argv[index + 1 :]
    )


def _envelope(
    command: ObservationCommand,
    *,
    result: Any | None = None,
    error: ObservationError | None = None,
    actions: list[Action] | None = None,
    truncated: bool = False,
) -> ObservationEnvelope[Any]:
    return ObservationEnvelope(
        request_id=_request_id(),
        generated_at=_now(),
        ok=error is None,
        command=command,
        result=result,
        error=error,
        next_actions=actions or [],
        meta=ObservationMeta(truncated=truncated),
    )


def _source_options(function: Callable[..., Any]) -> Callable[..., Any]:
    function = click.option("--registry-revision", default=None)(function)
    function = click.option("--as-of", required=True)(function)
    function = click.option("--source", type=click.Path(path_type=Path), required=True)(function)
    return function


def _actions(source: Path, as_of: str, *, code: str | None = None) -> list[Action]:
    argv = ["infralink", "validate", "--source", str(source), "--as-of", as_of]
    values = [action("validate", argv, "Validate the observation source")]
    if code:
        values.append(
            action(
                "explain",
                ["infralink", "explain", "{error_code}"],
                "Explain this diagnostic",
                bindings={
                    "error_code": Binding(
                        type="string", required=True, source=f"error.code ({code})"
                    )
                },
            )
        )
    return values


def run_validate(ctx: Any, source: Path, as_of: str, registry_revision: str | None) -> int:
    from infralink.observation import validate

    values = {
        "source": str(source),
        "as_of": as_of,
        "registry_revision": registry_revision,
    }
    command = _command(["validate"], values)
    report = validate([source], as_of=_parse_as_of(as_of), registry_revision=registry_revision)
    diagnostics = asdict(report.diagnostics)
    result = ObservationValidateResult(
        valid=report.valid,
        document_count=report.document_count,
        diagnostics=DiagnosticSetResult.model_validate(diagnostics),
    )
    if report.valid:
        _emit(ctx, _envelope(command, result=result, actions=_actions(source, as_of)))
        return 0
    first = next(
        (
            item
            for item in report.diagnostics.diagnostics
            if item.code in {"schema-version-missing", "schema-version-unsupported"}
        ),
        report.diagnostics.diagnostics[0],
    )
    error = ObservationError(code=first.code, message=first.message, details=diagnostics)
    _emit(
        ctx,
        _envelope(
            command,
            error=error,
            actions=_actions(source, as_of, code=first.code),
            truncated=report.diagnostics.truncated,
        ),
    )
    return 2 if first.code in {"schema-version-missing", "schema-version-unsupported"} else 1


def _project(source: Path, as_of: str, registry_revision: str | None) -> Any:
    from infralink.observation import project

    return project([source], as_of=_parse_as_of(as_of), registry_revision=registry_revision)


def _project_failure(
    ctx: Any, command: ObservationCommand, error: Exception, source: Path, as_of: str
) -> int:
    report = error.report  # type: ignore[attr-defined]
    first = report.diagnostics.diagnostics[0]
    _emit(
        ctx,
        _envelope(
            command,
            error=ObservationError(
                code=first.code, message=first.message, details=asdict(report.diagnostics)
            ),
            actions=_actions(source, as_of, code=first.code),
            truncated=report.diagnostics.truncated,
        ),
    )
    return 2 if first.code in {"schema-version-missing", "schema-version-unsupported"} else 1


@click.command(name="capabilities")
@click.pass_obj
def capabilities(ctx: Any) -> int:
    """Describe the offline observation contract surface."""
    result = CapabilitiesResult(
        document_schema_versions=["infralink.observation/v1"],
        plan_schema_versions=["infralink.plan.v1"],
        input_schemas={
            name: f"infralink/schemas/observation/v1/{name}.json"
            for name in (
                "profile",
                "instance",
                "application",
                "dependency",
                "secrets",
                "operations-view",
                "readiness-suite",
            )
        },
        evaluator_types={
            "health": [
                "http-status",
                "irc-handshake",
                "postgres-ready",
                "smtp-banner",
                "tcp-connect",
            ],
            "metrics": ["prometheus-scrape"],
            "logs": ["contains", "regex"],
        },
        projections=["observation", "secrets", "view", "readiness"],
    )
    _emit(ctx, _envelope(_command(["capabilities"], {}), result=result))
    return 0


@click.command(name="explain")
@click.argument("error_code")
@click.pass_obj
def explain_command(ctx: Any, error_code: str) -> int:
    from infralink.observation import DiagnosticCodeNotFoundError, explain

    command = _command(["explain"], {"error_code": error_code})
    try:
        value = explain(error_code)
    except DiagnosticCodeNotFoundError as error:
        _emit(
            ctx,
            _envelope(
                command,
                error=ObservationError(
                    code="diagnostic-code-not-found",
                    message=str(error),
                    details={"available_codes": list(error.available_codes)},
                ),
            ),
        )
        return 1
    _emit(ctx, _envelope(command, result=ExplainResult(**asdict(value))))
    return 0


@click.group(name="project")
def project_group() -> None:
    """Project observation contracts into deterministic plans."""


@project_group.command(name="observation")
@_source_options
@click.pass_obj
def project_observation(ctx: Any, source: Path, as_of: str, registry_revision: str | None) -> int:
    from infralink.observation import ProjectValidationError

    command = _command(
        ["project", "observation"],
        {"source": str(source), "as_of": as_of, "registry_revision": registry_revision},
    )
    try:
        projected = _project(source, as_of, registry_revision)
    except ProjectValidationError as error:
        return _project_failure(ctx, command, error, source, as_of)
    result = ProjectObservationResult(
        plan=ObservationPlan.model_validate(projected.plan.model_dump(mode="python")),
        sources=tuple(SourceProvenanceResult(**asdict(source)) for source in projected.sources),
    )
    _emit(ctx, _envelope(command, result=result, actions=_actions(source, as_of)))
    return 0


def _projection_command(kind: str) -> Callable[..., int]:
    def command(
        ctx: Any,
        source: Path,
        as_of: str,
        registry_revision: str | None,
        item_id: str | None = None,
    ) -> int:
        from infralink.observation import ProjectValidationError

        path = ["project", kind]
        values = {"source": str(source), "as_of": as_of, "registry_revision": registry_revision}
        if item_id is not None:
            values[f"{kind}_id"] = item_id
        context = _command(path, values)
        try:
            projected = _project(source, as_of, registry_revision)
        except ProjectValidationError as error:
            return _project_failure(ctx, context, error, source, as_of)
        plan = projected.plan
        if kind == "secrets":
            result: Any = ProjectSecretsResult(
                plan_digest=plan.plan_digest or "",
                secret_requirements=plan.secret_requirements,
                secret_bindings=plan.secret_bindings,
                provider_aliases=plan.provider_aliases,
                opaque_identities=plan.opaque_identities,
            )
        else:
            collection = plan.operations_views if kind == "view" else plan.readiness_suites
            selected = next((value for value in collection if value.id == item_id), None)
            if selected is None:
                code = f"{kind}-not-found"
                _emit(
                    ctx,
                    _envelope(
                        context,
                        error=ObservationError(
                            code=code,
                            message=f"{kind.title()} not found",
                            details={f"{kind}_id": item_id},
                        ),
                        actions=_actions(source, as_of, code=code),
                    ),
                )
                return 1
            result = (
                ProjectViewResult(plan_digest=plan.plan_digest or "", view=selected)
                if kind == "view"
                else ProjectReadinessResult(
                    plan_digest=plan.plan_digest or "",
                    readiness_suite=ObservationReadinessSuite.model_validate(
                        selected.model_dump(mode="python")
                    ),
                )
            )
        _emit(ctx, _envelope(context, result=result, actions=_actions(source, as_of)))
        return 0

    return command


project_secrets = click.command(name="secrets")(
    _source_options(click.pass_obj(_projection_command("secrets")))
)
project_view = click.command(name="view")(
    click.argument("item_id")(_source_options(click.pass_obj(_projection_command("view"))))
)
project_readiness = click.command(name="readiness")(
    click.argument("item_id")(_source_options(click.pass_obj(_projection_command("readiness"))))
)
project_group.add_command(project_secrets)
project_group.add_command(project_view)
project_group.add_command(project_readiness)
