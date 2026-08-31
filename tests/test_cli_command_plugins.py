"""External command plugins must join the one public Infralink tree."""

from __future__ import annotations

from types import SimpleNamespace

import click
import pytest
from click.testing import CliRunner

import infralink.cli.main as cli_main


def test_load_command_discovers_declared_external_controller_plugin(
    monkeypatch,
) -> None:
    controller = click.Group("controller")
    entry_point = SimpleNamespace(name="controller", load=lambda: controller)

    monkeypatch.setattr(
        "infralink.cli.command_plugins.entry_points",
        lambda *, group, name: (
            (entry_point,) if (group, name) == ("infralink.commands", "controller") else ()
        ),
    )

    assert cli_main._load_command("controller") is controller


def test_load_command_invokes_an_external_command_factory(monkeypatch) -> None:
    controller = click.Group("controller")
    entry_point = SimpleNamespace(name="controller", load=lambda: lambda: controller)

    monkeypatch.setattr(
        "infralink.cli.command_plugins.entry_points",
        lambda *, group, name: (
            (entry_point,) if (group, name) == ("infralink.commands", "controller") else ()
        ),
    )

    assert cli_main._load_command("controller") is controller


def test_load_command_rejects_a_plugin_with_the_wrong_public_name(monkeypatch) -> None:
    entry_point = SimpleNamespace(name="controller", load=lambda: click.Group("wrong"))

    monkeypatch.setattr(
        "infralink.cli.command_plugins.entry_points",
        lambda *, group, name: (
            (entry_point,) if (group, name) == ("infralink.commands", "controller") else ()
        ),
    )

    with pytest.raises(RuntimeError, match="command_plugin_name_invalid"):
        cli_main._load_command("controller")


def test_root_discovers_a_declared_external_command(monkeypatch) -> None:
    @click.command("controller")
    def controller() -> None:
        click.echo("controller-command")

    entry_point = SimpleNamespace(name="controller", load=lambda: controller)
    monkeypatch.setattr(
        "infralink.cli.command_plugins.entry_points",
        lambda *, group, name=None: (
            (entry_point,)
            if group == "infralink.commands" and (name is None or name == "controller")
            else ()
        ),
    )

    result = CliRunner().invoke(cli_main.cli, ["controller"])

    assert result.exit_code == 0, result.output
    assert result.output == "controller-command\n"
