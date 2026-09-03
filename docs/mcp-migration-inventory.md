# MCP Migration Inventory

## Current Public Surface

`infralink-mcp` is the complete native MCP surface. It is a transport launcher,
not a second command registry: every exposed tool is projected from the same
typed operation registry as `infralink`.

## Typed Foundation

Every public operation is registered once and projected as sibling generated
Click and MCP transports. Schema, invocation, and high-risk host-control tests
cover the shared contract.

## Deferred Cutover

[#270] removed the legacy generic MCP projection. New operations must be added
to the shared typed registry rather than directly to either transport.
