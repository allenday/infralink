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
from infralink.cli import command_plugins
from infralink.cli.main import _command_for_path, _help_parameters, cli

_TOOL_NAME = "infralink_command"
_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["schema_version", "ok", "command"],
    "additionalProperties": True,
}
_ROOT_SOURCE_PROPERTIES: dict[str, dict[str, str]] = {
    "registry": {
        "type": "string",
        "description": "Registry checkout root.",
    },
    "edges": {
        "type": "string",
        "description": "Edges YAML path.",
    },
}
_NATIVE_OPTION_NAMES: dict[tuple[tuple[str, ...], str], str | None] = {
    # Analyze predates the root-owned topology selector. Its local registry
    # override is redundant; its boolean artifact switch is distinct and must
    # not overload the root edges source path.
    (("analyze",), "registry"): None,
    (("analyze",), "edges"): "include_edges",
}
_NATIVE_GROUP_PATHS = frozenset({("diagram",)})
_SOURCE_INDEPENDENT_PATHS = frozenset({("diagram", "project")})


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
            if path in _NATIVE_GROUP_PATHS:
                paths[f"infralink_{'_'.join(path).replace('-', '_')}"] = path
            context = click.Context(command)
            for child_name in command.list_commands(context):
                if not path and child_name in command_plugins.names():
                    # External operations are added from their build manifest
                    # below; loading Click here would import the runtime app.
                    continue
                child = command.get_command(context, child_name)
                if child is not None:
                    visit(child, (*path, child_name))
            return
        if path != ("mcp", "serve"):
            paths[f"infralink_{'_'.join(path).replace('-', '_')}"] = path

    with command_plugins.discovery_scope():
        visit(cli, ())
        for operation in command_plugins.operations():
            path = operation.path
            if path == ("mcp", "serve"):
                continue
            name = f"infralink_{'_'.join(path).replace('-', '_')}"
            if name in paths:
                raise RuntimeError("command_plugin_path_conflict")
            paths[name] = path
    return paths


def _parameter_schema(parameter: click.Parameter) -> dict[str, Any]:
    """Project Click's parameter type, including bounded integers, to JSON Schema."""
    parameter_type = parameter.type
    schema: dict[str, Any]
    if isinstance(parameter_type, BoolParamType):
        schema = {"type": "boolean"}
    elif isinstance(parameter_type, IntParamType):
        schema = {"type": "integer"}
        if isinstance(parameter_type, click.IntRange):
            if parameter_type.min is not None:
                schema["minimum"] = parameter_type.min
            if parameter_type.max is not None:
                schema["maximum"] = parameter_type.max
    else:
        schema = {"type": "string"}
    if isinstance(parameter, click.Option) and parameter.multiple:
        return {"type": "array", "items": schema}
    return schema


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
    command = _command_for_path(path, allow_external_commands=False)
    if command is None:
        manifest_operation = command_plugins.operation(path)
        if manifest_operation is None:
            raise RuntimeError("command_plugin_operation_missing")
        return _manifest_native_tool(name, manifest_operation)
    arguments, options = _help_parameters(command)
    arguments_by_name = {
        parameter.name: parameter
        for parameter in command.params
        if isinstance(parameter, click.Argument)
    }
    # Every native projection may use the root-owned topology sources. They
    # must remain in the MCP schema even when a mounted child hides its local
    # adapter fields, otherwise MCP would lose a supported CLI capability.
    properties: dict[str, Any] = dict(_native_root_source_properties(path))
    required: list[str] = []
    for argument in arguments:
        argument_parameter = arguments_by_name[argument.name]
        properties[argument.name] = _parameter_schema(argument_parameter)
        if argument.required:
            required.append(argument.name)
    for option in options:
        native_name = _native_option_name(path, option.name)
        if native_name is None:
            continue
        option_parameter = _option_parameter(command, option.name)
        properties[native_name] = _parameter_schema(option_parameter)
        if option.required:
            required.append(native_name)
    if path == ("help",):
        properties["path"] = {"type": "array", "items": {"type": "string"}}
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


def _manifest_native_tool(name: str, operation: command_plugins.ManifestOperation) -> Tool:
    """Project a generated Agent Surface manifest without importing its app."""
    input_schema = operation.operation["input_schema"]
    output_schema = operation.operation["output_schema"]
    if not isinstance(input_schema, dict) or not isinstance(output_schema, dict):
        raise RuntimeError("command_plugin_manifest_invalid")
    raw_properties = input_schema.get("properties", {})
    raw_required = input_schema.get("required", [])
    if not isinstance(raw_properties, dict) or not isinstance(raw_required, list):
        raise RuntimeError("command_plugin_manifest_invalid")
    properties: dict[str, Any] = dict(_ROOT_SOURCE_PROPERTIES)
    properties.update(
        {
            field: schema
            for field, schema in raw_properties.items()
            if field not in _ROOT_SOURCE_PROPERTIES
        }
    )
    return Tool(
        name=name,
        title="Infralink " + " ".join(operation.path),
        description=operation.summary,
        input_schema={
            "type": "object",
            "properties": properties,
            "required": [field for field in raw_required if field not in _ROOT_SOURCE_PROPERTIES],
            "additionalProperties": False,
        },
        output_schema=_OUTPUT_SCHEMA,
    )


def _native_tools() -> list[Tool]:
    with command_plugins.discovery_scope():
        return [_native_tool(name, path) for name, path in _native_paths().items()]


def _native_argv(name: str, arguments: Any) -> list[str]:
    if not isinstance(arguments, dict):
        raise ValueError("MCP tool arguments must be an object")
    path = _native_paths().get(name)
    if path is None:
        raise ValueError(f"Unknown tool: {name}")
    command = _command_for_path(path)
    assert command is not None
    root_sources = _native_root_source_argv(arguments, path)
    if path == ("help",):
        unknown = set(arguments) - {"path", *set(_native_root_source_properties(path))}
        if unknown:
            raise ValueError(f"Unknown tool arguments: {', '.join(sorted(unknown))}")
        help_path = arguments.get("path", [])
        if not isinstance(help_path, list) or any(
            not isinstance(item, str) or not item for item in help_path
        ):
            raise ValueError("path must be an array of non-empty strings")
        return [*root_sources, "help", *help_path]
    positional, options = _help_parameters(command)
    named_options = {
        native_name: option
        for option in options
        if (native_name := _native_option_name(path, option.name)) is not None
    }
    allowed = (
        set(_native_root_source_properties(path))
        | {item.name for item in positional}
        | set(named_options)
    )
    unknown = set(arguments) - allowed
    if unknown:
        raise ValueError(f"Unknown tool arguments: {', '.join(sorted(unknown))}")
    argv: list[str] = [*root_sources, *path]
    for argument in positional:
        value = arguments.get(argument.name)
        if argument.required and (not isinstance(value, str) or not value):
            raise ValueError(f"{argument.name} must be a non-empty string")
        if value is not None:
            argv.append(str(value))
    for native_name, option in named_options.items():
        value = arguments.get(native_name)
        if option.required and value is None:
            raise ValueError(f"{native_name} is required")
        option_parameter = _option_parameter(command, option.name)
        if option_parameter.multiple:
            if (
                not isinstance(value, list)
                or not value
                or any(not isinstance(item, str) or not item for item in value)
            ):
                raise ValueError(f"{native_name} must be a non-empty array of strings")
            for item in value:
                argv.extend(["--" + option.name.replace("_", "-"), item])
            continue
        if isinstance(option_parameter.type, BoolParamType):
            if value is None:
                continue
            if not isinstance(value, bool):
                raise ValueError(f"{native_name} must be a boolean")
            _append_boolean_option(argv, option_parameter, value)
        elif value is not None:
            argv.extend(["--" + option.name.replace("_", "-"), str(value)])
    return argv


def _native_option_name(path: tuple[str, ...], name: str) -> str | None:
    """Return the public MCP name for a command-local option."""
    return _NATIVE_OPTION_NAMES.get((path, name), name)


def _append_boolean_option(argv: list[str], option: click.Option, value: bool) -> None:
    """Serialize Click's positive/negative boolean flags without changing defaults."""
    if value:
        argv.append(_long_option(option.opts, option.name))
    elif option.secondary_opts:
        argv.append(_long_option(option.secondary_opts, option.name))
    elif bool(option.default):
        raise ValueError(f"{option.name} cannot be set to false")


def _long_option(options: list[str], name: str | None) -> str:
    return next((option for option in options if option.startswith("--")), f"--{name}")


def _native_root_source_properties(path: tuple[str, ...]) -> dict[str, dict[str, str]]:
    return {} if path in _SOURCE_INDEPENDENT_PATHS else _ROOT_SOURCE_PROPERTIES


def _native_root_source_argv(arguments: dict[str, Any], path: tuple[str, ...] = ()) -> list[str]:
    """Emit the sole CLI topology selectors before the native command path."""
    argv: list[str] = []
    for name in _native_root_source_properties(path):
        value = arguments.get(name)
        if value is None:
            continue
        if not isinstance(value, str) or not value:
            raise ValueError(f"{name} must be a non-empty string")
        argv.extend((f"--{name}", value))
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
        # Agent Surface Click projections synchronously own their invocation
        # loop. Run the shared CLI path outside this MCP event loop so native
        # typed operations and legacy Click commands have identical transport.
        payload = await asyncio.to_thread(invoke_cli, argv, stdin)
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
