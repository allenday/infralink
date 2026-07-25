"""Documentation generation CLI command."""

from __future__ import annotations

import html
from pathlib import Path

import click

from infralink.cli.actions import action
from infralink.cli.artifacts import (
    artifact_fingerprint,
    artifact_metadata,
    artifact_pages,
    artifact_usage,
    continuation_actions,
    require_output,
    write_artifacts,
)
from infralink.cli.contracts import ArtifactResult, ArtifactSummary
from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.main import (
    Context,
    _context_for,
    _emit,
    _page_options,
    _root_source_argv,
    pass_context,
)
from infralink.cli.output import ok_envelope


def _document_bytes(markdown: str, output_format: str) -> tuple[str, bytes]:
    if output_format == "markdown":
        return "text/markdown", markdown.encode("utf-8")
    document = (
        '<!doctype html>\n<html><head><meta charset="utf-8"></head>'
        f"<body><pre>{html.escape(markdown)}</pre></body></html>\n"
    )
    return "text/html", document.encode("utf-8")


@click.command()
@click.option("--output", "-o", type=click.Path(path_type=Path), required=True)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["markdown", "html"]),
    default="markdown",
)
@click.option("--host", "-h", "host_filter")
@click.option("--index-only", is_flag=True)
@_page_options
@pass_context
def docs(
    ctx: Context,
    output: Path | None,
    output_format: str,
    host_filter: str | None,
    index_only: bool,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Generate infrastructure documentation."""
    output = require_output(output)
    from infralink.generators.markdown import (
        generate_edge_index,
        generate_host_doc,
        generate_index,
    )

    registry = ctx.registry
    edges = ctx.edges
    extension = "md" if output_format == "markdown" else "html"
    generated: list[tuple[Path, str, bytes]] = []
    media_type, body = _document_bytes(generate_index(registry, edges), output_format)
    generated.append((Path(f"index.{extension}"), media_type, body))

    if not index_only:
        hosts = registry.active_hosts()
        if host_filter:
            host = registry.get(host_filter)
            if host is None:
                raise CliFailure(
                    code=ErrorCode.ENTITY_NOT_FOUND,
                    message="Host was not found",
                    exit_code=3,
                    fix="Use infralink hosts to list available hosts",
                    details={"entity_type": "host", "requested_id": host_filter},
                    next_actions=[
                        action(
                            "list",
                            [*_root_source_argv(ctx), "hosts"],
                            "List available hosts",
                        )
                    ],
                )
            hosts = [host]
        for host in sorted(hosts, key=lambda item: item.canonical_name):
            if Path(host.canonical_name).name != host.canonical_name:
                raise artifact_usage("Host name cannot be used as a safe artifact path")
            media_type, body = _document_bytes(
                generate_host_doc(host, edges, registry),
                output_format,
            )
            generated.append((Path(f"{host.canonical_name}.{extension}"), media_type, body))
        if len(edges) > 0:
            media_type, body = _document_bytes(
                generate_edge_index(edges, registry),
                output_format,
            )
            generated.append((Path("edges") / f"index.{extension}", media_type, body))

    artifacts = artifact_metadata(output, generated)
    selected = collection or "artifacts"
    fingerprint = artifact_fingerprint(
        command="docs",
        sources=[path for path in (ctx.registry_path, ctx.edges_path) if path is not None],
        options={
            "format": output_format,
            "host": host_filter,
            "index_only": index_only,
            "output": output.as_posix(),
        },
        collections={"artifacts": artifacts},
    )
    pages = artifact_pages(
        command="docs",
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
        "docs",
        "--output",
        output.as_posix(),
        "--format",
        output_format,
    ]
    if host_filter is not None:
        base_argv.extend(["--host", host_filter])
    if index_only:
        base_argv.append("--index-only")
    actions = [
        action("help", ["infralink", "help", "docs"], "Show docs help"),
        *continuation_actions(
            base_argv=base_argv,
            limit=limit,
            pages=pages,
            sources={"artifacts": "result.artifacts.page.next_cursor"},
        ),
    ]
    payload = ok_envelope(_context_for(path=["docs"]), result, actions)
    payload["meta"]["truncated"] = pages["artifacts"].page.next_cursor is not None
    _emit(payload)
