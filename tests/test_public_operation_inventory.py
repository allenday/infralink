"""The public operation registry must remain complete and transport-neutral."""

import asyncio

import click
from mcp import Client

from infralink.cli.main import cli
from infralink.mcp_server import create_server
from infralink.operator_surface import operator_surface

# This is the retained typed surface for #270.  Legacy aliases, the generic
# argv MCP bridge and controller-local executables are deliberately absent:
# they are not alternate public routes to this registry.
RETAINED_OPERATIONS = frozenset(
    {
        "analyze",
        "app.list",
        "app.show",
        "capabilities",
        "check",
        "diagram",
        "doctor",
        "docs",
        "edge.list",
        "edge.show",
        "explain",
        "fleet.validate",
        "host.apply",
        "host.bootstrap",
        "host.create",
        "host.list",
        "host.logs",
        "host.show",
        "host.status",
        "host.verifier",
        "help",
        "info",
        "operation.status",
        "project.observation",
        "project.readiness",
        "project.secrets",
        "project.view",
        "registry.host.get",
        "registry.host.patch",
        "release.inspect",
        "release.inspect-attestation",
        "release.render-publisher-request",
        "release.validate-candidate",
        "resolve",
        "secrets.inspect",
        "service.list",
        "service.show",
        "version",
        "topology.diagram",
    }
)

TRANSPORT_ONLY_COMMANDS = frozenset()


def test_public_operator_surface_has_one_complete_operation_registry() -> None:
    assert {
        operation.name for operation in operator_surface.operations.list()
    } == RETAINED_OPERATIONS


def test_typed_help_is_generated_from_the_public_operation_registry() -> None:
    result = asyncio.run(operator_surface.invoke("help", {"path": "host"}))

    assert result.path == ["host"]
    assert {item.name for item in result.children} == {
        "apply",
        "bootstrap",
        "create",
        "list",
        "logs",
        "show",
        "status",
        "verifier",
    }
    assert all(
        item.action.command.startswith("infralink help --path host.") for item in result.children
    )


def test_typed_help_has_mcp_parity() -> None:
    from infralink.operator_surface import operator_mcp_adapter

    async def exercise() -> None:
        async with Client(operator_mcp_adapter().server) as client:
            response = await client.call_tool("help", {"path": "host.show"})
        assert response.is_error is False
        assert response.structured_content["result"]["path"] == ["host", "show"]

    asyncio.run(exercise())


def test_public_click_and_mcp_are_projected_from_the_same_registry() -> None:
    context = click.Context(cli)
    assert set(cli.list_commands(context)) - {"actions", "operations"} == {
        name.split(".", 1)[0] for name in RETAINED_OPERATIONS
    }
    assert TRANSPORT_ONLY_COMMANDS == set()
    assert create_server().name == "infralink"
