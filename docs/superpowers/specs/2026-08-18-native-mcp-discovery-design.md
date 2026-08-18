# Native MCP Discovery and Inspection Design

## Goal

Make common read-only Infralink operations discoverable and typed to an MCP
client without creating another command parser, response schema, registry
reader, or execution path.

## Context

The existing MCP server exposes one tool, `infralink_command(argv, stdin?)`.
It correctly invokes the normal Click CLI with JSON output, but a caller must
first discover command grammar and construct a shell-like argument list. The
CLI already owns command metadata, help contracts, dispatch, validation, and
HATEOAS responses. That remains authoritative.

## Decision

Expose one native MCP tool for every non-recursive CLI command path. Tool names
are mechanically derived as `infralink_<path_joined_by_underscores>`.

Each native method builds canonical CLI tokens from the command registry and calls `invoke_cli()`. It
does not read registry files, call a provider, reproduce CLI validation, or
construct a response envelope. The typed method response is therefore the same
CLI HATEOAS payload the equivalent command returns, serialized as MCP
structured content.

`infralink_command` remains available for arbitrary argv compatibility. Native
tools include explicit write/apply operations and preserve their CLI safeguards.
Only `mcp serve` is excluded because it recursively invokes the MCP transport.

## Command Mapping

| MCP tool | Canonical CLI argv |
| --- | --- |
| `infralink_help()` | `help` |
| `infralink_host_status(host_ref=ref)` | `host status REF` |
| `infralink_registry_host_patch(host_ref=ref, set=value, write=true)` | `registry host patch REF --set VALUE --write` |

Generated input schemas reject invalid field types before dispatch. Domain validation
and all successful/error result envelopes come from the CLI.

## Discovery and Size

The server instructions direct clients to `infralink_help`. Its root response
is the existing compact CLI help result, including child commands and valid
HATEOAS next actions. A path returns that command's existing documented
arguments/options. No MCP method emits a full static command tree or unlimited
logs; `infralink_host_logs` reuses the CLI's bounded last-run mode.

## Error Contract

Transport argument errors produce MCP tool errors. CLI/domain errors produce
the normal `infralink.cli/v1` envelope with `ok: false`, its stable error code,
and CLI-generated `next_actions`. This preserves one repair vocabulary for CLI
and MCP clients.

## Tests

Protocol tests will assert that the MCP server advertises the native tools and
that each native invocation is semantically equivalent to the corresponding
CLI invocation. Tests will also reject malformed typed arguments and prove the
generic command tool still exists.

## Non-goals

- A native method per CLI leaf.
- Direct registry/provider access from MCP transport.
- A second HATEOAS or JSON schema family.
- Native write, apply, bootstrap, or release actions.
