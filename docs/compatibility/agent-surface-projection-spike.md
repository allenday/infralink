# Agent Surface Projection Spike

Issue: #202

Status: unavailable for production integration; proof path passes against the existing native MCP
projection.

## Scope

This spike evaluated `infralink operation status <operation_id>` as the only projection target.
It did not add `agent-surface` as a required dependency, change supported Python versions, replace
Click command wiring, or introduce a second command grammar.

## Evidence

- The CLI envelope remains the source of truth for `schema_version`, `ok`, `command`, `result`,
  `error`, `fix`, `next_actions`, and `meta`.
- MCP structured content can project the same operation-status envelope without field drift.
- The operation-status success proof keeps resolved registry context and renders the bounded,
  redacted, safe `status` next action.
- The legacy `op_...` operation-id path keeps the existing structured `provider_unavailable`
  error envelope.
- Unsupported projection targets fail closed with a typed `usage_error` envelope.
- Mutating operations remain outside the proof. `host apply` is represented only as unsupported,
  so no production mutation path or confirmation shortcut is introduced.
- `agent-surface` import checks fail closed below Python 3.12 and when version 0.1.0 is missing
  or mismatched.

## Recommendation

Do not add a production `agent-surface` dependency from this spike.

The existing native MCP transport already preserves the YAML-first CLI/HATEOAS contract by invoking
the CLI and returning its JSON envelope as structured MCP content. If a later integration is
approved, make `agent-surface` an optional Python 3.12+ extra or a separate adapter package, then
keep contract tests that compare projected MCP structured content against the authoritative CLI
envelope.

Issue 202 can be closed as an evaluation outcome rather than a production defect. A follow-up issue
is only warranted if maintainers want an optional Python 3.12+ `agent-surface` adapter.

## Verification

Focused verification:

```bash
ruff check src/infralink/cli/projection_spike.py tests/test_agent_surface_projection_spike.py
pytest -o addopts='' tests/test_agent_surface_projection_spike.py tests/test_mcp_server.py
```
