# MCP Migration Inventory

## Current Public Surface

`infralink mcp serve` remains the established complete public MCP surface while
the canonical registry is introduced incrementally. This avoids removing valid
tools before their operation families have migrated.

## Typed Foundation

The read-only `app.list` and `app.show` family is registered once and has
generated Click and MCP projections used by its contract tests. The public
server is intentionally not switched to that partial projection yet.

## Deferred Cutover

[#270] completes the remaining command-family migrations, proves complete
CLI/registry/MCP parity, and then replaces the legacy public MCP projection.
