"""Native stdio MCP transport for the Infralink CLI contract."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import click
from click.testing import CliRunner
from click.types import BoolParamType, IntParamType
from mcp.server import InitializationOptions, NotificationOptions, Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.types import CallToolRequestParams, CallToolResult, ListToolsResult, TextContent, Tool

from infralink import __version__
from infralink.cli.main import _command_for_path, _help_parameters, cli

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


def _native_paths() -> dict[str, tuple[str, ...]]:
    paths: dict[str, tuple[str, ...]] = {}

    def visit(command: click.Command, path: tuple[str, ...]) -> None:
        if isinstance(command, click.Group):
            context = click.Context(command)
            for child_name in command.list_commands(context):
                child = command.get_command(context, child_name)
                if child is not None:
                    visit(child, (*path, child_name))
            return
        if path != ("mcp", "serve"):
            paths[f"infralink_{'_'.join(path).replace('-', '_')}"] = path

    visit(cli, ())
    return paths


def _parameter_schema(parameter: click.Parameter) -> dict[str, Any]:
    """Project Click's parameter type, including bounded integers, to JSON Schema."""
    parameter_type = parameter.type
    if isinstance(parameter_type, BoolParamType):
        return {"type": "boolean"}
    if isinstance(parameter_type, IntParamType):
        schema: dict[str, Any] = {"type": "integer"}
        if isinstance(parameter_type, click.IntRange):
            if parameter_type.min is not None:
                schema["minimum"] = parameter_type.min
            if parameter_type.max is not None:
                schema["maximum"] = parameter_type.max
        return schema
    return {"type": "string"}


def _option_parameter(command: click.Command, name: str) -> click.Option:
    """Find a Click option by its public long-option descriptor name."""
    for parameter in command.params:
        if not isinstance(parameter, click.Option):
            continue
        long_option = next(
            (option for option in parameter.opts if option.startswith("--")),
            parameter.name or "",
        )
        if long_option.removeprefix("--").replace("-", "_") == name:
            return parameter
    raise ValueError(f"No Click option matches public descriptor: {name}")


def _native_tool(name: str, path: tuple[str, ...]) -> Tool:
    command = _command_for_path(path)
    assert command is not None
    arguments, options = _help_parameters(command)
    arguments_by_name = {
        parameter.name: parameter
        for parameter in command.params
        if isinstance(parameter, click.Argument)
    }
    properties: dict[str, Any] = {}
    required: list[str] = []
    for argument in arguments:
        parameter = arguments_by_name[argument.name]
        properties[argument.name] = _parameter_schema(parameter)
        if argument.required:
            required.append(argument.name)
    for option in options:
        parameter = _option_parameter(command, option.name)
        properties[option.name] = _parameter_schema(parameter)
        if option.required:
            required.append(option.name)
    if path == ("help",):
        properties = {"path": {"type": "array", "items": {"type": "string"}}}
        required = []
    return Tool(
        name=name,
        title="Infralink " + " ".join(path),
        description=command.help or command.short_help or "",
        input_schema={
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        output_schema=_OUTPUT_SCHEMA,
    )


def _native_tools() -> list[Tool]:
    return [_native_tool(name, path) for name, path in _native_paths().items()]


def _native_argv(name: str, arguments: Any) -> list[str]:
    if not isinstance(arguments, dict):
        raise ValueError("MCP tool arguments must be an object")
    path = _native_paths().get(name)
    if path is None:
        raise ValueError(f"Unknown tool: {name}")
    command = _command_for_path(path)
    assert command is not None
    if path == ("help",):
        help_path = arguments.get("path", [])
        if not isinstance(help_path, list) or any(
            not isinstance(item, str) or not item for item in help_path
        ):
            raise ValueError("path must be an array of non-empty strings")
        return ["help", *help_path]
    positional, options = _help_parameters(command)
    allowed = {item.name for item in positional} | {item.name for item in options}
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"Unknown tool arguments: {', '.join(sorted(unknown))}")
    argv: list[str] = list(path)
    for argument in positional:
        value = arguments.get(argument.name)
        if argument.required and (not isinstance(value, str) or not value):
            raise ValueError(f"{argument.name} must be a non-empty string")
        if value is not None:
            argv.append(str(value))
    for option in options:
        value = arguments.get(option.name)
        if option.required and value is None:
            raise ValueError(f"{option.name} is required")
        if isinstance(value, bool):
            if value:
                argv.append("--" + option.name.replace("_", "-"))
        elif value is not None:
            argv.extend(["--" + option.name.replace("_", "-"), str(value)])
    return argv


async def _list_tools(_context: ServerRequestContext[Any], _params: Any) -> ListToolsResult:
    return ListToolsResult(tools=[_tool(), *_native_tools()])


async def _call_tool(
    _context: ServerRequestContext[Any], params: CallToolRequestParams
) -> CallToolResult:
    if params.name != _TOOL_NAME and params.name not in _native_paths():
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
