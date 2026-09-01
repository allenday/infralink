"""External command manifests must join the one public Infralink tree."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from agent_surface import App
from agent_surface.manifest import manifest_for
from click.testing import CliRunner
from pydantic import BaseModel

import infralink.cli.main as cli_main


class _Request(BaseModel):
    value: str = "default"


class _Result(BaseModel):
    value: str


def _app(operation_name: str = "controller.doctor") -> App:
    app = App("infralink")

    @app.operation(operation_name, summary="Inspect controller evidence", read_only=True)
    def doctor(request: _Request) -> _Result:
        return _Result(value=request.value)

    return app


def _install_manifest(monkeypatch: pytest.MonkeyPatch, *, loader=None) -> SimpleNamespace:
    app = _app()
    entry_point = SimpleNamespace(
        name="controller",
        value="example.controller:build_app",
        load=loader or (lambda: _app),
    )
    monkeypatch.setattr(
        "infralink.cli.command_plugins.entry_points",
        lambda *, group: (entry_point,) if group == "infralink.commands" else (),
    )
    monkeypatch.setattr(
        "infralink.cli.command_plugins.installed_manifests",
        lambda: (
            manifest_for(
                app,
                factory=entry_point.value,
                distribution_name="infralink-ops",
                distribution_version="0.0.0",
            ),
        ),
    )
    return entry_point


def test_root_and_help_discover_a_manifest_backed_command_without_importing_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_manifest(
        monkeypatch,
        loader=lambda: pytest.fail("discovery must not import an external command app"),
    )

    root = CliRunner().invoke(cli_main.cli, ["--output", "json"])
    root_help = CliRunner().invoke(cli_main.cli, ["--output", "json", "help"])
    help_result = CliRunner().invoke(cli_main.cli, ["--output", "json", "help", "controller"])
    click_help = CliRunner().invoke(cli_main.cli, ["--output", "json", "controller", "--help"])

    assert root.exit_code == 0, root.output
    assert '"name":"controller"' in root.output
    assert root_help.exit_code == 0, root_help.output
    assert '"name":"controller","summary":"Inspect controller evidence"' in root_help.output
    assert help_result.exit_code == 0, help_result.output
    assert '"name":"doctor"' in help_result.output
    assert click_help.exit_code == 0, click_help.output
    assert '"name":"doctor"' in click_help.output


def test_discovery_refreshes_after_an_in_place_plugin_upgrade(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry_point = SimpleNamespace(
        name="controller",
        value="example.controller:build_app",
        load=lambda: _app(),
    )
    manifests = [
        manifest_for(
            _app(),
            factory=entry_point.value,
            distribution_name="infralink-ops",
            distribution_version="0.0.0",
        )
    ]
    monkeypatch.setattr(
        "infralink.cli.command_plugins.entry_points",
        lambda *, group: (entry_point,) if group == "infralink.commands" else (),
    )
    monkeypatch.setattr(
        "infralink.cli.command_plugins.installed_manifests", lambda: tuple(manifests)
    )

    assert cli_main.command_plugins.names() == {"controller"}

    entry_point.name = "platform"
    entry_point.value = "example.platform:build_app"
    manifests[:] = [
        manifest_for(
            _app("platform.doctor"),
            factory=entry_point.value,
            distribution_name="infralink-ops",
            distribution_version="0.0.1",
        )
    ]

    assert cli_main.command_plugins.names() == {"platform"}


def test_explicit_execution_imports_and_verifies_the_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_manifest(monkeypatch)

    result = CliRunner().invoke(cli_main.cli, ["controller", "doctor", "--value", "verified"])

    assert result.exit_code == 0, result.output
    assert "verified" in result.output


def test_execution_rejects_a_factory_that_does_not_match_its_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wrong = App("infralink")
    _install_manifest(monkeypatch, loader=lambda: wrong)

    with pytest.raises(RuntimeError, match="command_plugin_manifest_mismatch"):
        cli_main._load_command("controller")


def test_packaging_entry_points_do_not_reclassify_builtin_commands() -> None:
    # Core historically advertised a few built-ins through package metadata.
    # Their actual Click implementations remain built-in command surfaces.
    assert cli_main._is_external_command("diagram") is False
