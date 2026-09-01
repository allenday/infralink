"""Discovery must not depend on controller-runtime availability."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner
from mcp import Client

import infralink.cli.main as cli_main
from infralink.mcp_server import create_server


def _deny_side_effect(*args: object, **kwargs: object) -> None:
    raise AssertionError(f"discovery attempted a side effect: {args!r} {kwargs!r}")


def _pulling_plugin(marker: Path) -> SimpleNamespace:
    return SimpleNamespace(
        name="controller",
        load=lambda: (
            marker.write_text("external plugin loaded", encoding="utf-8"),
            subprocess.run(["docker", "pull", "example.invalid/controller:latest"]),
        ),
    )


@pytest.fixture
def deny_discovery_side_effects(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setattr(subprocess, "run", _deny_side_effect)
    monkeypatch.setattr(socket, "create_connection", _deny_side_effect)
    monkeypatch.setattr(socket, "getaddrinfo", _deny_side_effect)
    marker = tmp_path / "external-plugin-loaded"
    plugin = _pulling_plugin(marker)
    monkeypatch.setattr(
        "infralink.cli.command_plugins.entry_points",
        lambda *, group, name=None: (
            (plugin,)
            if group == "infralink.commands" and (name is None or name == "controller")
            else ()
        ),
    )
    return marker


@pytest.mark.parametrize(
    ("argv", "expected_exit_code"),
    [
        ((), 0),
        (("help",), 0),
        (("--help",), 0),
        (("version",), 0),
        (("--version",), 0),
        (("controller", "--help"), 2),
        (("help", "controller"), 2),
    ],
)
def test_cli_discovery_is_offline_and_side_effect_free(
    deny_discovery_side_effects: Path,
    argv: tuple[str, ...],
    expected_exit_code: int,
) -> None:
    result = CliRunner().invoke(cli_main.cli, ["--output", "json", *argv])

    assert result.exit_code == expected_exit_code, result.output
    assert not deny_discovery_side_effects.exists()


def test_mcp_capability_discovery_is_offline_and_side_effect_free(
    deny_discovery_side_effects: Path,
) -> None:
    async def discover() -> set[str]:
        async with Client(create_server()) as client:
            tools = await client.list_tools()
        return {tool.name for tool in tools.tools}

    names = asyncio.run(discover())

    assert "infralink_command" in names
    assert "infralink_controller" not in names
    assert not deny_discovery_side_effects.exists()
