"""Application topology query commands."""

from __future__ import annotations

import click

from infralink.cli.main import (
    Context,
    _active_collection,
    _emit_query_result,
    _next_cursors,
    _page_offset,
    _page_options,
    _topology_fingerprint,
    pass_context,
)
from infralink.cli.pagination import CursorCodec
from infralink.cli.queries import list_apps as query_list_apps
from infralink.cli.queries import show_app as query_show_app


@click.group()
def app() -> None:
    """Inspect application groupings."""


@app.command(name="list")
@_page_options
@pass_context
def list_apps(
    ctx: Context,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """List application groupings."""
    selected = _active_collection(collection, cursor, ("items",))
    fingerprint = _topology_fingerprint(ctx, include_registry=True, include_edges=True)
    offset = _page_offset(
        command="app list",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = query_list_apps(
        ctx.registry,
        ctx.edges,
        limit=limit,
        offset=offset,
        next_cursor=CursorCodec().encode("app list", "items", offset + limit, fingerprint),
    )
    _emit_query_result(
        path=["app", "list"],
        command_argv=["app", "list"],
        result=result,
        limit=limit,
    )


@app.command(name="show")
@click.argument("app_id")
@_page_options
@pass_context
def show_app(
    ctx: Context,
    app_id: str,
    limit: int,
    cursor: str | None,
    collection: str | None,
) -> None:
    """Show one application grouping."""
    collections = ("services", "edges")
    selected = _active_collection(collection, cursor, collections)
    fingerprint = _topology_fingerprint(
        ctx,
        include_registry=True,
        include_edges=True,
        identifiers={"app_id": app_id},
    )
    offset = _page_offset(
        command="app show",
        collection=selected,
        cursor=cursor,
        fingerprint=fingerprint,
    )
    result = query_show_app(
        ctx.registry,
        ctx.edges,
        app_id,
        collection=selected,
        limit=limit,
        offset=offset,
        next_cursors=_next_cursors("app show", collections, selected, offset, limit, fingerprint),
    )
    _emit_query_result(
        path=["app", "show"],
        command_argv=["app", "show", app_id],
        result=result,
        limit=limit,
    )
