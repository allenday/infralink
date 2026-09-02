# Registry Optional Input Resolution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Resolve optional topology companions from one Registry checkout without environment-specific paths or ambient host configuration.

**Architecture:** A shared resolver owns explicit-argument precedence and bounded companion discovery from the selected checkout root. Typed operations consume that resolver for edge declarations. The legacy Doctor command reuses the same source policy for observation companions and derives a unique declared Gatus endpoint only when live evidence requires it.

**Tech Stack:** Python 3.12, Pydantic, Click, Agent Surface, pytest.

---

### Task 1: Resolve Registry edge companions generically

**Files:**
- Modify: `src/infralink/operator_sources.py`
- Test: `tests/test_operator_sources.py`

- [ ] **Step 1: Write failing source-resolution tests**

```python
def test_load_sources_discovers_one_declared_edge_file(tmp_path: Path) -> None:
    # A Registry checkout with exactly one `edges.yml` uses that declaration.
    assert load_sources(SourceRequest(registry=tmp_path)).edges_path == edges.resolve()

def test_load_sources_rejects_ambiguous_edge_files(tmp_path: Path) -> None:
    with pytest.raises(OperationError, match="ambiguous"):
        load_sources(SourceRequest(registry=tmp_path))
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest tests/test_operator_sources.py -q`

Expected: failure because `load_sources` still requires `network/main-dev/edges/edges.yml`.

- [ ] **Step 3: Implement the bounded generic companion resolver**

```python
def resolve_registry_companion(root: Path, *, filename: str) -> Path:
    candidates = tuple(sorted(root.glob(f"**/{filename}")))
    if len(candidates) == 1:
        return candidates[0]
    raise OperationError(...)
```

Use an explicit `request.edges` unchanged. For an omitted edge input, resolve exactly one declared `edges.yml`; missing and ambiguous results must be typed `configuration_required` errors with an explicit caller action.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `pytest tests/test_operator_sources.py -q`

Expected: PASS.

### Task 2: Derive Doctor live-observation endpoint from Registry declarations

**Files:**
- Modify: `src/infralink/cli/doctor.py`
- Test: `tests/test_cli_doctor.py`

- [ ] **Step 1: Write failing Doctor tests**

```python
def test_doctor_derives_unique_declared_gatus_endpoint(...) -> None:
    result = runner.invoke(cli, ["--registry", str(checkout), "doctor", "host", host, ...])
    assert observed_request_url == "https://gatus.example.test/api/v1/endpoints/statuses"

def test_doctor_rejects_ambiguous_declared_gatus_endpoints(...) -> None:
    assert payload["error"]["code"] == "configuration_required"
```

- [ ] **Step 2: Run the focused tests and verify they fail**

Run: `pytest tests/test_cli_doctor.py -q`

Expected: failure because omitted `--gatus-url` currently always requires an environment value.

- [ ] **Step 3: Implement declaration-only Gatus endpoint selection**

```python
def _declared_gatus_url(ctx: Context) -> str | None:
    # inspect Registry services only; return one HTTP(S) endpoint or no value
```

Explicit `--gatus-url` remains authoritative. A zero or non-unique declared candidate remains a typed configuration error; no host-local default, network probe, or compatibility alias is introduced. Preserve the resolved source in the response envelope and replayable action argv.

- [ ] **Step 4: Run focused tests and verify they pass**

Run: `pytest tests/test_cli_doctor.py -q`

Expected: PASS.

### Task 3: Validate public transport contracts

**Files:**
- Modify: `tests/test_cli_root.py`
- Modify: `tests/test_mcp_server.py` only if native MCP needs a regression fixture

- [ ] **Step 1: Add a failing public-command regression**

```python
def test_root_registry_selection_resolves_edges_for_generated_operations(...) -> None:
    # `host apply --dry-run` and its HATEOAS action require only --registry.
```

- [ ] **Step 2: Run the focused regression and verify it fails**

Run: `pytest tests/test_cli_root.py -q`

Expected: failure only if the root adapter does not project the new resolved companion source.

- [ ] **Step 3: Make the smallest root/adapter integration change required**

Keep source selection central and preserve explicit `--edges` precedence. Do not add public flags, new executables, controller behavior, or direct MCP configuration inputs.

- [ ] **Step 4: Run the focused public suite and verify it passes**

Run: `pytest tests/test_cli_root.py tests/test_mcp_server.py -q`

Expected: PASS.

### Task 4: Full verification, review, and delivery

**Files:**
- Modify: generated schemas only if the existing generator produces a contract diff

- [ ] **Step 1: Run static and focused contract checks**

Run: `ruff check src tests && mypy src && pytest tests/test_operator_sources.py tests/test_cli_doctor.py tests/test_cli_root.py tests/test_mcp_server.py -q`

Expected: PASS.

- [ ] **Step 2: Run the repository test suite**

Run: `pytest -q`

Expected: PASS with only the repository's known skips.

- [ ] **Step 3: Obtain independent complete-diff critique**

The critic must confirm generic checkout-only resolution, explicit input precedence, bounded ambiguity failures, source-qualified action replay, and no controller/network/secret regression.

- [ ] **Step 4: Commit and create issue-linked PR**

Run: `git add ... && git commit -m "feat(sources): infer optional registry companions"`

Open a focused PR with `Refs #240`, not an auto-close keyword. Record its immutable head for IDD critique and CI evidence.
