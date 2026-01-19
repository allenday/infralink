"""Documentation generation CLI command."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from infralink.cli.main import Context, pass_context

console = Console()


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
        generate_host_doc,
        generate_index,
        generate_edge_index,
    )

    try:
        registry = ctx.registry
        edges = ctx.edges
    except click.ClickException as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    output.mkdir(parents=True, exist_ok=True)
    generated_count = 0

    # Generate index
    index_content = generate_index(registry, edges)
    index_file = output / "index.md"
    index_file.write_text(index_content)
    generated_count += 1
    console.print(f"[green]Generated:[/green] {index_file}")

    if index_only:
        console.print(f"\n[bold]Generated {generated_count} file(s)[/bold]")
        return

    # Generate per-host documentation
    hosts = registry.active_hosts()
    if host_filter:
        host = registry.get(host_filter)
        if not host:
            console.print(f"[red]Host not found:[/red] {host_filter}")
            raise SystemExit(1)
        hosts = [host]

    for host in hosts:
        doc_content = generate_host_doc(host, edges, registry)
        doc_file = output / f"{host.canonical_name}.md"
        doc_file.write_text(doc_content)
        generated_count += 1

        if ctx.verbose:
            console.print(f"[dim]Generated:[/dim] {doc_file}")

    # Generate edge index
    if len(edges) > 0:
        edge_dir = output.parent / "edges"
        edge_dir.mkdir(parents=True, exist_ok=True)
        edge_index = generate_edge_index(edges, registry)
        edge_file = edge_dir / "index.md"
        edge_file.write_text(edge_index)
        generated_count += 1
        console.print(f"[green]Generated:[/green] {edge_file}")

    console.print(f"\n[bold]Generated {generated_count} file(s) in {output}[/bold]")
