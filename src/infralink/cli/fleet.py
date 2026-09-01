"""Read-only declared fleet validation command."""

from __future__ import annotations

import click

from infralink.cli.actions import action
from infralink.cli.main import Context, _context_for, _emit, pass_context
from infralink.cli.output import ok_envelope
from infralink.operator_surface import FleetValidateRequest, fleet_validate


@click.group()
def fleet() -> None:
    """Inspect declared fleet topology without reconciling hosts."""


@fleet.command()
@click.option("--host", default=None, help="Filter by active host canonical name")
@click.option("--strict", is_flag=True, help="Treat warnings as validation failures")
@click.option("--live", "live", is_flag=True, help="Request registered read-only live evidence")
@pass_context
def validate(ctx: Context, host: str | None, strict: bool, live: bool) -> int:
    """Validate roles, services, and database-edge declarations."""
    if ctx.registry_path is None:
        raise click.UsageError("A registry source is required")
    result = fleet_validate(
        FleetValidateRequest(
            registry=ctx.registry_path,
            edges=ctx.edges_path,
            host=host,
            strict=strict,
            live=live,
        )
    )
    command_argv = ["fleet", "validate"]
    if host is not None:
        command_argv.extend(("--host", host))
    if strict:
        command_argv.append("--strict")
    if live:
        command_argv.append("--live")
    next_actions = []
    if not result.valid:
        next_actions.append(
            action(
                "inspect-declaration",
                [
                    "infralink",
                    "--registry",
                    str(ctx.registry_path),
                    *(["--edges", str(ctx.edges_path)] if ctx.edges_path is not None else []),
                    *command_argv,
                ],
                "Inspect the bounded declaration diagnostics before controller reconciliation",
            )
        )
    context_argv = ["--registry", str(ctx.registry_path)]
    if ctx.edges_path is not None:
        context_argv.extend(("--edges", str(ctx.edges_path)))
    context_argv.extend(command_argv)
    _emit(ok_envelope(_context_for(context_argv), result, next_actions))
    return 0 if result.valid else 1
