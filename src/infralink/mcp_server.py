"""Native stdio MCP transport for Infralink's one typed operation registry."""

from __future__ import annotations

import asyncio
from typing import Any


def create_server() -> Any:
    """Return the Agent Surface MCP projection of the public operation registry."""
    from infralink.operator_surface import operator_mcp_adapter

    return operator_mcp_adapter().server


def serve() -> None:
    """Serve the native MCP projection over stdio."""
    from infralink.operator_surface import operator_mcp_adapter

    asyncio.run(operator_mcp_adapter().run_stdio())


def run() -> None:
    """Console entry point for the native stdio transport."""
    serve()


__all__ = ["create_server", "run", "serve"]
