# Native MCP Discovery and Inspection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a typed read-only MCP surface without adding a second dispatch or response path.

**Architecture:** Native tools compile typed MCP arguments to canonical CLI argv and call `invoke_cli()`. `mcp_server.py` owns transport schemas and argument conversion only; Click remains the single domain command parser and HATEOAS envelope producer.

**Tech Stack:** Python 3.10+, MCP SDK, Click, Pytest.

---

### Task 1: Define native tool contracts and dispatch mapping

**Files:**
- Modify: `src/infralink/mcp_server.py`
- Test: `tests/test_mcp_server.py`

- [ ] Add failing protocol assertions that `list_tools()` returns `infralink_command`, `infralink_help`, `infralink_doctor`, `infralink_host_get`, `infralink_host_list`, `infralink_host_status`, and `infralink_host_logs`.
- [ ] Run `python -m pytest --no-cov -q tests/test_mcp_server.py::test_mcp_protocol_discovers_native_tools`; expect failure because only `infralink_command` exists.
- [ ] Define immutable tool metadata and typed argument schemas in `mcp_server.py`; map every native method to a function that emits the canonical CLI argv shown in the design document.
- [ ] Route native and generic tools through one `_call_tool` result serializer that returns `invoke_cli()` payload as structured MCP content.
- [ ] Run the focused test; expect pass.
- [ ] Commit with `feat(mcp): expose typed read-only operator tools`.

### Task 2: Prove typed dispatch and error equivalence

**Files:**
- Modify: `tests/test_mcp_server.py`
- Modify: `src/infralink/mcp_server.py`

- [ ] Add failing tests comparing `infralink_help`, `infralink_doctor`, and host tool results against `invoke_cli()` for the equivalent argv. Compare `ok`, `command.parsed`, `result` or `error`, and `next_actions`.
- [ ] Add failing tests that invalid typed values are MCP transport errors and that CLI domain errors remain typed Infralink envelopes.
- [ ] Run `python -m pytest --no-cov -q tests/test_mcp_server.py`; expect failures for missing typed dispatch/error validation.
- [ ] Implement only the converters and validation required for those tests. Do not read registry files or invoke non-CLI business logic.
- [ ] Run the focused test; expect pass.
- [ ] Commit with `test(mcp): prove native dispatch equivalence`.

### Task 3: Verify server contract and repository gates

**Files:**
- Modify only generated schemas if the existing generator changes them.
- Test: `tests/test_mcp_server.py`

- [ ] Run `python -m pytest -q`.
- [ ] Run `python -m ruff format --check src tests scripts`, `python -m ruff check src tests scripts`, and `python -m mypy src scripts`.
- [ ] Run `python scripts/generate_cli_schemas.py`, `python scripts/generate_observation_schemas.py`, `python scripts/generate_release_schemas.py`, and `python scripts/check_docs.py`; require a clean diff.
- [ ] Request independent review before opening the PR.
