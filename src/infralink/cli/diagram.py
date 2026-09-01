"""Legacy artifact diagrams and read-only V2 topology projection."""

from __future__ import annotations

from pathlib import Path
from typing import Literal, cast

import click
from agent_surface import OperationError
from pydantic import ValidationError

from infralink.cli.actions import action
from infralink.cli.artifacts import (
    artifact_fingerprint,
    artifact_metadata,
    artifact_pages,
    continuation_actions,
    require_output,
    write_artifacts,
)
from infralink.cli.contracts import ArtifactResult, ArtifactSummary
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.main import (
    Context,
    _context_for,
    _emit,
    _page_options,
    _root_source_argv,
    pass_context,
)
from infralink.cli.output import ok_envelope
from infralink.operator_surface import DiagramProjectRequest, diagram_project


class _LegacyDiagramOutputOption(click.Option):
    """Expose legacy requiredness without imposing it on V2 child parsing."""

    required_for_projection = True


@click.group(invoke_without_command=True)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["mermaid", "d2", "dot", "all"]),
    default="mermaid",
    help="Output format",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    required=False,
    cls=_LegacyDiagramOutputOption,
)
@click.option("--group", "-g", "filter_group")
@click.option("--include-terminated", is_flag=True)
@_page_options
@pass_context
def diagram(
    ctx: Context,
    output_format: str,
    output: Path | None,
    filter_group: str | None,
    include_terminated: bool,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Generate legacy artifacts or project a read-only V2 topology graph."""
    if click.get_current_context().invoked_subcommand is not None:
        if output is not None or filter_group is not None or include_terminated:
            raise CliFailure(
                code=ErrorCode.DIAGRAM_PROJECT_FORBIDDEN_INPUT,
                message="diagram project accepts no legacy artifact inputs",
                exit_code=ExitCode.USAGE_ERROR,
                fix="Use only --source, --scope, --host, --service, and --syntax with diagram project.",
            )
        return
    _legacy_diagram(
        ctx, output_format, output, filter_group, include_terminated, limit, cursor, collection
    )


def _legacy_diagram(
    ctx: Context,
    output_format: str,
    output: Path | None,
    filter_group: str | None,
    include_terminated: bool,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Preserve the legacy artifact-writing diagram callback without alteration."""
    output = require_output(output)
    from infralink.generators.d2 import generate_d2
    from infralink.generators.dot import generate_dot
    from infralink.generators.mermaid import generate_mermaid

    registry = ctx.registry
    edges = ctx.edges
    if filter_group:
        hosts = [host for host in registry if host.group == filter_group]
    elif include_terminated:
        hosts = list(registry)
    else:
        hosts = registry.active_hosts()

    generators = {
        "mermaid": (generate_mermaid, Path("infrastructure.md"), "text/markdown"),
        "d2": (generate_d2, Path("infrastructure.d2"), "text/vnd.d2"),
        "dot": (generate_dot, Path("infrastructure.dot"), "text/vnd.graphviz"),
    }
    formats = tuple(generators) if output_format == "all" else (output_format,)
    generated = [
        (filename, media_type, generator(hosts, edges, registry).encode("utf-8"))
        for name in formats
        for generator, filename, media_type in [generators[name]]
    ]
    artifacts = artifact_metadata(output, generated)
    selected = collection or "artifacts"
    fingerprint = artifact_fingerprint(
        command="diagram",
        sources=[path for path in (ctx.registry_path, ctx.edges_path) if path is not None],
        options={
            "format": output_format,
            "output": output.as_posix(),
            "group": filter_group,
            "include_terminated": include_terminated,
        },
        collections={"artifacts": artifacts},
    )
    pages = artifact_pages(
        command="diagram",
        collections={"artifacts": artifacts},
        selected=selected,
        cursor=cursor,
        limit=limit,
        fingerprint=fingerprint,
    )
    write_artifacts(output, generated)
    result = ArtifactResult(
        artifacts=pages["artifacts"],
        summary=ArtifactSummary(artifact_count=len(artifacts)),
    )
    base_argv = [
        *_root_source_argv(ctx),
        "diagram",
        "--output",
        output.as_posix(),
        "--format",
        output_format,
    ]
    if filter_group is not None:
        base_argv.extend(["--group", filter_group])
    if include_terminated:
        base_argv.append("--include-terminated")
    actions = [
        action("help", ["infralink", "help", "diagram"], "Show diagram help"),
        *continuation_actions(
            base_argv=base_argv,
            limit=limit,
            pages=pages,
            sources={"artifacts": "result.artifacts.page.next_cursor"},
        ),
    ]
    payload = ok_envelope(_context_for(path=["diagram"]), result, actions)
    payload["meta"]["truncated"] = pages["artifacts"].page.next_cursor is not None
    _emit(payload)


@diagram.command(name="project")
@click.option("--source", "source", type=click.Path(path_type=Path), required=True, multiple=True)
@click.option("--scope", type=click.Choice(["full", "host", "service"]), default="full")
@click.option("--host", default=None)
@click.option("--service", default=None)
@click.option("--syntax", type=click.Choice(["mermaid", "dot"]), default="mermaid")
@pass_context
def project(
    ctx: Context,
    source: tuple[Path, ...],
    scope: str,
    host: str | None,
    service: str | None,
    syntax: str,
) -> int:
    """Project declared V2 topology as bounded inline Mermaid or DOT source."""
    if ctx.registry_path is not None or ctx.edges_path is not None:
        raise CliFailure(
            code=ErrorCode.DIAGRAM_PROJECT_FORBIDDEN_INPUT,
            message="diagram project accepts no registry or edge inputs",
            exit_code=ExitCode.USAGE_ERROR,
            fix="Use explicit --source declarations without --registry or --edges.",
        )
    try:
        request = DiagramProjectRequest(
            source=source,
            scope=cast(Literal["full", "host", "service"], scope),
            host=host,
            service=service,
            syntax=cast(Literal["mermaid", "dot"], syntax),
        )
    except ValidationError as error:
        raise CliFailure(
            code=ErrorCode.DIAGRAM_SCOPE_SELECTOR_INVALID,
            message="diagram project scope requires its exact selector combination",
            exit_code=ExitCode.USAGE_ERROR,
            fix="Use full with no selector, host with --host, or service with --service <host_uuid>/<service_instance_id>.",
        ) from error
    try:
        result = diagram_project(request)
    except OperationError as error:
        code = (
            ErrorCode.DIAGRAM_RENDER_BOUNDS_EXCEEDED
            if error.code == "diagram_render_bounds_exceeded"
            else ErrorCode.DIAGRAM_SOURCE_INVALID
        )
        raise CliFailure(
            code=code,
            message=error.message,
            exit_code=ExitCode.INPUT_ERROR,
            fix=error.fix or "Supply valid V2 observation source declarations.",
            details=error.details[0] if error.details else {},
        ) from None
    argv = ["diagram", "project"]
    for path in source:
        argv.extend(("--source", str(path)))
    if scope != "full":
        argv.extend(("--scope", scope))
    if host is not None:
        argv.extend(("--host", host))
    if service is not None:
        argv.extend(("--service", service))
    if syntax != "mermaid":
        argv.extend(("--syntax", syntax))
    _emit(
        ok_envelope(
            _context_for(argv, ignore_root_sources=True),
            result,
            [
                action(
                    "help", ["infralink", "help", "diagram", "project"], "Show diagram project help"
                )
            ],
        )
    )
    return 0
