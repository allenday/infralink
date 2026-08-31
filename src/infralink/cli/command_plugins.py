"""Optional public commands supplied by installed Infralink runtime packages."""

from __future__ import annotations

from importlib.metadata import entry_points

import click

_GROUP = "infralink.commands"


def names() -> set[str]:
    """Return declared external command names without importing their packages."""
    return {entry_point.name for entry_point in entry_points(group=_GROUP) if entry_point.name}


def load(name: str) -> click.Command | None:
    """Load one explicitly declared external command with a stable public name."""
    matches = tuple(entry_points(group=_GROUP, name=name))
    if not matches:
        return None
    if len(matches) != 1:
        raise RuntimeError("command_plugin_ambiguous")
    loaded = matches[0].load()
    if isinstance(loaded, click.Command):
        command = loaded
    elif callable(loaded):
        command = loaded()
    else:
        raise RuntimeError("command_plugin_invalid")
    if not isinstance(command, click.Command):
        raise RuntimeError("command_plugin_invalid")
    if command.name != name:
        raise RuntimeError("command_plugin_name_invalid")
    return command
