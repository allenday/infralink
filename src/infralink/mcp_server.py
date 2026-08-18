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
_NATIVE_TOOL_NAMES = frozenset(
    {
        "infralink_help",
        "infralink_doctor",
        "infralink_host_get",
        "infralink_host_list",
        "infralink_host_status",
        "infralink_host_logs",
    }
)
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


def _native_tools() -> list[Tool]:
    return [
        Tool(
            name="infralink_help",
            title="Infralink help",
            description="Discover CLI commands from the canonical command registry.",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "array", "items": {"type": "string"}}},
                "additionalProperties": False,
            },
            output_schema=_OUTPUT_SCHEMA,
        ),
        Tool(
            name="infralink_doctor",
            title="Infralink doctor",
            description="Inspect declared and live evidence for a host, service, edge, or profile.",
            input_schema={
                "type": "object",
                "properties": {
                    "target_type": {
                        "type": "string",
                        "enum": ["host", "service", "edge", "profile"],
                    },
                    "target_ref": {"type": "string"},
                    "validate": {"type": "boolean"},
                    "verbose": {"type": "boolean"},
                },
                "additionalProperties": False,
            },
            output_schema=_OUTPUT_SCHEMA,
        ),
        Tool(
            name="infralink_host_get",
            title="Get host declaration",
            description="Read one registry host declaration.",
            input_schema={
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
                "additionalProperties": False,
            },
            output_schema=_OUTPUT_SCHEMA,
        ),
        Tool(
            name="infralink_host_list",
            title="List hosts",
            description="List registry hosts.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
            output_schema=_OUTPUT_SCHEMA,
        ),
        Tool(
            name="infralink_host_status",
            title="Host status",
            description="Inspect the target-scoped reconcile status.",
            input_schema={
                "type": "object",
                "properties": {"ref": {"type": "string"}},
                "required": ["ref"],
                "additionalProperties": False,
            },
            output_schema=_OUTPUT_SCHEMA,
        ),
        Tool(
            name="infralink_host_logs",
            title="Host logs",
            description="Read bounded target-scoped host logs.",
            input_schema={
                "type": "object",
                "properties": {"ref": {"type": "string"}, "last_run": {"type": "boolean"}},
                "required": ["ref"],
                "additionalProperties": False,
            },
            output_schema=_OUTPUT_SCHEMA,
        ),
    ]


def _native_argv(name: str, arguments: Any) -> list[str]:
    if not isinstance(arguments, dict):
        raise ValueError("MCP tool arguments must be an object")

    def required_ref() -> str:
        ref = arguments.get("ref")
        if not isinstance(ref, str) or not ref:
            raise ValueError("ref must be a non-empty string")
        return ref

    if name == "infralink_help":
        path = arguments.get("path", [])
        if not isinstance(path, list) or any(
            not isinstance(item, str) or not item for item in path
        ):
            raise ValueError("path must be an array of non-empty strings")
        return ["help", *path]
    if name == "infralink_doctor":
        target_type = arguments.get("target_type")
        target_ref = arguments.get("target_ref")
        if (target_type is None) != (target_ref is None):
            raise ValueError("target_type and target_ref must be supplied together")
        if target_type is not None and target_type not in {"host", "service", "edge", "profile"}:
            raise ValueError("target_type must be host, service, edge, or profile")
        if target_ref is not None and (not isinstance(target_ref, str) or not target_ref):
            raise ValueError("target_ref must be a non-empty string")
        argv = ["doctor"]
        if arguments.get("verbose") is True:
            argv.insert(0, "--verbose")
        if target_type is not None:
            assert isinstance(target_ref, str)
            argv.extend([target_type, target_ref])
        if arguments.get("validate") is True:
            argv.append("--validate")
        return argv
    if name == "infralink_host_get":
        return ["registry", "host", "get", required_ref()]
    if name == "infralink_host_list":
        return ["host", "list"]
    if name == "infralink_host_status":
        return ["host", "status", required_ref()]
    if name == "infralink_host_logs":
        argv = ["host", "logs", required_ref()]
        if arguments.get("last_run") is True:
            argv.append("--last-run")
        return argv
    raise ValueError(f"Unknown tool: {name}")


async def _list_tools(_context: ServerRequestContext[Any], _params: Any) -> ListToolsResult:
    return ListToolsResult(tools=[_tool(), *_native_tools()])


async def _call_tool(
    _context: ServerRequestContext[Any], params: CallToolRequestParams
) -> CallToolResult:
    if params.name != _TOOL_NAME and params.name not in _NATIVE_TOOL_NAMES:
        return CallToolResult(
            content=[TextContent(text=f"Unknown tool: {params.name}")],
            is_error=True,
        )
    try:
        if params.name == _TOOL_NAME:
            argv, stdin = _arguments(params.arguments)
        else:
            argv, stdin = _native_argv(params.name, params.arguments), None
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
