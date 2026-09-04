"""CLI commands for infralink."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from click import Group

__all__ = ["cli"]


def __getattr__(name: str) -> Group:
    if name == "cli":
        from infralink.cli.main import cli

        return cli
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
