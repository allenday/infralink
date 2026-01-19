"""Diagram generation CLI command."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from infralink.cli.main import Context, pass_context

console = Console()


@click.command()
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
    default=Path("docs/diagrams"),
    help="Output directory",
)
@click.option(
    "--group",
    "-g",
    "filter_group",
    help="Filter to specific group",
)
@click.option(
    "--include-terminated",
    is_flag=True,
    help="Include terminated hosts",
)
@click.option(
    "--stdout",
    is_flag=True,
    help="Print to stdout instead of file",
)
@pass_context
def diagram(
    ctx: Context,
    output_format: str,
    output: Path,
    filter_group: str | None,
    include_terminated: bool,
    stdout: bool,
) -> None:
    """
    Generate infrastructure diagrams.

    Creates visual diagrams from registry and edge declarations.

    Examples:

        # Generate Mermaid diagram
        infralink diagram

        # Generate D2 diagram to stdout
        infralink diagram --format d2 --stdout

        # Generate all formats for a specific group
        infralink diagram --format all --group bdsmlr
    """
    from infralink.generators.mermaid import generate_mermaid
    from infralink.generators.d2 import generate_d2
    from infralink.generators.dot import generate_dot

    try:
        registry = ctx.registry
        edges = ctx.edges
    except click.ClickException as e:
        console.print(f"[red]Error:[/red] {e}")
        raise SystemExit(1)

    # Filter hosts
    if filter_group:
        hosts = [h for h in registry if h.group == filter_group]
    elif include_terminated:
        hosts = list(registry)
    else:
        hosts = registry.active_hosts()

    if not hosts:
        console.print("[yellow]No hosts match filter criteria[/yellow]")
        return

    # Generate diagrams
    generators = {
        "mermaid": (generate_mermaid, "infrastructure.md"),
        "d2": (generate_d2, "infrastructure.d2"),
        "dot": (generate_dot, "infrastructure.dot"),
    }

    formats_to_generate = list(generators.keys()) if output_format == "all" else [output_format]

    for fmt in formats_to_generate:
        generator, filename = generators[fmt]
        content = generator(hosts, edges, registry)

        if stdout:
            console.print(f"\n[bold]--- {fmt.upper()} ---[/bold]")
            console.print(content)
        else:
            output.mkdir(parents=True, exist_ok=True)
            output_file = output / filename
            output_file.write_text(content)
            console.print(f"[green]Generated:[/green] {output_file}")

    if not stdout:
        console.print(f"\n[bold]Diagrams written to:[/bold] {output}")
