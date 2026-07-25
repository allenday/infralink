"""Documentation generation CLI command."""

from __future__ import annotations

from pathlib import Path

import click

from infralink.cli.errors import CliFailure
from infralink.cli.main import Context, _emit, pass_context
from infralink.cli.output import error_envelope, ok_envelope


@click.command()
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=Path("docs/hosts"),
    help="Output directory for documentation",
)
@click.option(
    "--format",
    "-f",
    "output_format",
    type=click.Choice(["markdown", "html"]),
    default="markdown",
    help="Output format",
)
@click.option(
    "--host",
    "-h",
    "host_filter",
    help="Generate docs for specific host only",
)
@click.option(
    "--index-only",
    is_flag=True,
    help="Generate only the index file",
)
@pass_context
def docs(
    ctx: Context,
    output: Path,
    output_format: str,
    host_filter: str | None,
    index_only: bool,
) -> None:
    """
    Generate infrastructure documentation.

    Creates Markdown documentation for hosts and edges.

    Examples:

        # Generate all documentation
        infralink docs

        # Generate docs for specific host
        infralink docs --host relaxgg-bastion

        # Generate only index
        infralink docs --index-only
    """
    from infralink.generators.markdown import (
        generate_edge_index,
        generate_host_doc,
        generate_index,
    )

    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
        edges = ctx.edges
    except CliFailure:
        raise
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "DOCS_FAILED",
            "Ensure registry/edges paths are correct.",
            [{"command": "infralink validate", "description": "Validate registry and edges"}],
        )
        _emit(payload)
        raise SystemExit(1) from exc

    output.mkdir(parents=True, exist_ok=True)
    outputs: list[str] = []

    # Generate index
    index_content = generate_index(registry, edges)
    index_file = output / "index.md"
    index_file.write_text(index_content)
    outputs.append(str(index_file))

    if index_only:
        payload = ok_envelope(
            command,
            {"outputs": outputs, "count": len(outputs)},
            [{"command": "infralink docs", "description": "Generate full docs"}],
        )
        _emit(payload)
        return

    # Generate per-host documentation
    hosts = registry.active_hosts()
    if host_filter:
        host = registry.get(host_filter)
        if not host:
            payload = error_envelope(
                command,
                f"Host not found: {host_filter}",
                "DOCS_HOST_NOT_FOUND",
                "Use infralink hosts to list available hosts.",
                [{"command": "infralink hosts", "description": "List all hosts"}],
            )
            _emit(payload)
            raise SystemExit(1)
        hosts = [host]

    for host in hosts:
        doc_content = generate_host_doc(host, edges, registry)
        doc_file = output / f"{host.canonical_name}.md"
        doc_file.write_text(doc_content)
        outputs.append(str(doc_file))

    # Generate edge index
    if len(edges) > 0:
        edge_dir = output.parent / "edges"
        edge_dir.mkdir(parents=True, exist_ok=True)
        edge_index = generate_edge_index(edges, registry)
        edge_file = edge_dir / "index.md"
        edge_file.write_text(edge_index)
        outputs.append(str(edge_file))

    payload = ok_envelope(
        command,
        {"outputs": outputs, "count": len(outputs)},
        [
            {"command": "infralink diagram", "description": "Generate diagrams"},
            {"command": "infralink analyze", "description": "Analyze topology coverage"},
        ],
    )
    _emit(payload)
