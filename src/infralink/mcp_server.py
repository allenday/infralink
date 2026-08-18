"""Native stdio MCP transport for the Infralink CLI contract."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from click.testing import CliRunner
from mcp.server import InitializationOptions, NotificationOptions, Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

from infralink import __version__
from infralink.cli.main import cli

_TOOL_NAME = "infralink_command"
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "ok", "command"],
    "additionalProperties": True,
}


def _arguments(value: Any) -> tuple[list[str], str | None]:
    if not isinstance(value, dict):
        raise ValueError("MCP tool arguments must be an object")
    argv = value.get("argv")
    if not isinstance(argv, list) or not argv or any(not isinstance(item, str) for item in argv):
        raise ValueError("argv must be a non-empty array of strings")
    if any(
        token == "--output" or token.startswith("--output=") or token.startswith("-o")
        for token in argv
    ):
        raise ValueError("argv must not set output format; MCP always returns structured JSON")
    if "mcp" in argv:
        raise ValueError("MCP cannot invoke its own server command")
    stdin = value.get("stdin")
    if stdin is not None and not isinstance(stdin, str):
        raise ValueError("stdin must be a string when provided")
    return argv, stdin


def invoke_cli(argv: list[str], stdin: str | None = None) -> dict[str, Any]:
    """Invoke the normal CLI as JSON without shell interpretation."""
    safe_argv, safe_stdin = _arguments({"argv": argv, "stdin": stdin})
    result = CliRunner().invoke(cli, ["--output", "json", *safe_argv], input=safe_stdin)
    try:
        payload = json.loads(result.output)
    except json.JSONDecodeError as error:
        raise RuntimeError("Infralink command did not return a JSON contract") from error
    if not isinstance(payload, dict):
        raise RuntimeError("Infralink command returned an invalid JSON contract")
    return payload


def _tool() -> Tool:
    return Tool(
        name=_TOOL_NAME,
        title="Infralink command",
        description=(
            "Run one typed Infralink CLI command. Start with argv ['help'] or ['help', '<command>']; "
            "the returned Infralink envelope contains HATEOAS next_actions. This is not a shell. "
            "Writes retain the CLI's explicit --write or --apply requirements."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "argv": {
                    "type": "array",
                    "description": "Infralink command tokens, excluding the executable and --output json.",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "stdin": {
                    "type": "string",
                    "description": "Optional standard input for a command that explicitly requests it.",
                },
            },
            "required": ["argv"],
            "additionalProperties": False,
        },
        output_schema=_OUTPUT_SCHEMA,
    )


async def _list_tools(_context: ServerRequestContext[Any], _params: Any) -> ListToolsResult:
    return ListToolsResult(tools=[_tool()])


async def _call_tool(
    _context: ServerRequestContext[Any], params: CallToolRequestParams
) -> CallToolResult:
    if params.name != _TOOL_NAME:
        return CallToolResult(
            content=[TextContent(text=f"Unknown tool: {params.name}")],
            is_error=True,
        )
    try:
        argv, stdin = _arguments(params.arguments)
        payload = invoke_cli(argv, stdin)
    except (RuntimeError, ValueError) as error:
        return CallToolResult(content=[TextContent(text=str(error))], is_error=True)
    return CallToolResult(
        content=[TextContent(text=json.dumps(payload, separators=(",", ":"), sort_keys=True))],
        structured_content=payload,
        is_error=not bool(payload.get("ok")),
    )


def create_server() -> Server[Any]:
    return Server(
        "infralink",
        version=__version__,
        title="Infralink",
        description="Typed Infralink operator tools over stdio MCP.",
        instructions="Use infralink_command with argv ['help'] before unfamiliar operations.",
        on_list_tools=_list_tools,
        on_call_tool=_call_tool,
    )


async def _serve() -> None:
    server = create_server()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="infralink",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(), experimental_capabilities={}
                ),
            ),
        )


def serve() -> None:
    """Serve the native stdio MCP transport until its client disconnects."""
    asyncio.run(_serve())
