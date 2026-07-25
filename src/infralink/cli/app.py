from __future__ import annotations

import click

from infralink.cli.errors import CliFailure
from infralink.cli.main import Context, _emit, pass_context
from infralink.cli.output import error_envelope, ok_envelope


@click.group()
def app() -> None:
    """Manage application groupings."""
    pass


@app.command(name="list")
@pass_context
def list_apps(ctx: Context) -> None:
    """List all application groupings."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
        apps = registry.applications
    except CliFailure:
        raise
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "APP_LIST_FAILED",
            "Ensure registry and applications.yml are valid.",
            [],
        )
        _emit(payload)
        raise SystemExit(1) from exc

    app_payload = []
    for application in sorted(apps, key=lambda a: a.id):
        app_payload.append(
            {
                "id": application.id,
                "description": application.description,
                "member_count": len(application.schema.members),
            }
        )

    result = {"applications": app_payload, "count": len(app_payload)}
    payload = ok_envelope(
        command,
        result,
        [
            {"command": "infralink app show <id>", "description": "Show app details"},
        ],
    )
    _emit(payload)


@app.command(name="show")
@click.argument("app_id")
@pass_context
def show_app(ctx: Context, app_id: str) -> None:
    """Show details for a specific application."""
    command = click.get_current_context().command_path.replace("cli", "infralink")
    try:
        registry = ctx.registry
        application = registry.applications.get_application(app_id)
        if not application:
            raise click.ClickException(f"Application not found: {app_id}")

        edges = application.resolve_edges(registry, ctx.edges)
    except CliFailure:
        raise
    except Exception as exc:
        payload = error_envelope(
            command,
            str(exc),
            "APP_SHOW_FAILED",
            f"Check if app {app_id} exists.",
            [],
        )
        _emit(payload)
        raise SystemExit(1) from exc

    result = application.to_dict()
    result["resolved_edges"] = [
        {
            "id": e.id,
            "type": e.type.value,
            "from_service": e.source_service,
            "to_service": e.target_service,
            "to_host": e.target_host,
        }
        for e in edges
    ]

    payload = ok_envelope(
        command,
        result,
        [
            {"command": "infralink app list", "description": "List all apps"},
        ],
    )
    _emit(payload)
