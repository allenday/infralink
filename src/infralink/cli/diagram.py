"""Diagram generation CLI command."""

from __future__ import annotations

from pathlib import Path

import click

from infralink.cli.actions import action
from infralink.cli.artifacts import (
    artifact_fingerprint,
    artifact_pages,
    continuation_actions,
    require_output,
    write_artifacts,
)
from infralink.cli.contracts import ArtifactResult
from infralink.cli.main import (
    Context,
    _context_for,
    _emit,
    _page_options,
    _root_source_argv,
    pass_context,
)
from infralink.cli.output import ok_envelope


@click.command()
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["mermaid", "d2", "dot", "all"]),
    default="mermaid",
    help="Output format",
)
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True)
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
    """Generate infrastructure diagrams."""
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
    artifacts = write_artifacts(output, generated)
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
    result = ArtifactResult(
        artifacts=pages["artifacts"],
        summary={"artifact_count": len(artifacts)},
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
