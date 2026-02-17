# Agent-First CLI JSON Output Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Convert infralink CLI to JSON-only, agent-first output with HATEOAS `next_actions` for all commands.

**Architecture:** Introduce a small CLI output helper that builds JSON envelopes and handles errors. Refactor each Click command to return structured data (no Rich), then emit envelopes via the helper. Root command returns a self-documenting command tree and registry/edge summary. Tests use Click’s CliRunner to assert JSON envelopes and error shape.

**Tech Stack:** Python (Click), pytest, standard json.

### Task 1: Add output helper + base tests for envelope

**Files:**
- Create: `src/infralink/cli/output.py`
- Create: `tests/test_cli_output.py`

**Step 1: Write the failing test**

```python
import json
from infralink.cli.output import ok_envelope, error_envelope


def test_ok_envelope_shape():
    payload = ok_envelope("infralink validate", {"valid": True}, [{"command": "infralink check", "description": "Run checks"}])
    assert payload["ok"] is True
    assert payload["command"] == "infralink validate"
    assert payload["result"] == {"valid": True}
    assert payload["next_actions"][0]["command"] == "infralink check"


def test_error_envelope_shape():
    payload = error_envelope("infralink validate", "boom", "VALIDATION_FAILED", "Fix it", [])
    assert payload["ok"] is False
    assert payload["error"]["code"] == "VALIDATION_FAILED"
    assert payload["fix"] == "Fix it"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_output.py -v`
Expected: FAIL (module not found)

**Step 3: Write minimal implementation**

```python
import json
from typing import Any


def ok_envelope(command: str, result: Any, next_actions: list[dict[str, str]]):
    return {"ok": True, "command": command, "result": result, "next_actions": next_actions}


def error_envelope(command: str, message: str, code: str, fix: str, next_actions: list[dict[str, str]]):
    return {
        "ok": False,
        "command": command,
        "error": {"message": message, "code": code},
        "fix": fix,
        "next_actions": next_actions,
    }
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_output.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/infralink/cli/output.py tests/test_cli_output.py
git commit -m "feat(cli): add JSON envelope helpers"
```

### Task 2: Root command JSON + self-documenting tree

**Files:**
- Modify: `src/infralink/cli/main.py`
- Create: `tests/test_cli_root.py`

**Step 1: Write failing test**

```python
import json
from click.testing import CliRunner
from infralink.cli.main import cli


def test_root_command_returns_json_tree():
    runner = CliRunner()
    result = runner.invoke(cli, [])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["command"] == "infralink"
    assert "commands" in payload["result"]
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_root.py -v`
Expected: FAIL (non-JSON output)

**Step 3: Implement minimal JSON root output**
- Remove Rich console usage.
- Build a command tree list with `name`, `description`, `usage`.
- Return envelope via `click.echo(json.dumps(...))`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_root.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/infralink/cli/main.py tests/test_cli_root.py
git commit -m "feat(cli): JSON root command tree"
```

### Task 3: JSON errors and standardized output for `validate`

**Files:**
- Modify: `src/infralink/cli/validate.py`
- Create: `tests/test_cli_validate.py`

**Step 1: Write failing test**

```python
import json
from click.testing import CliRunner
from infralink.cli.main import cli


def test_validate_returns_json_envelope():
    runner = CliRunner()
    result = runner.invoke(cli, ["validate", "--registry", "missing.yml"])
    payload = json.loads(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert "fix" in payload
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_validate.py -v`
Expected: FAIL

**Step 3: Implement JSON output**
- Replace Rich output with JSON envelope.
- For errors, return `{ ok: false, error: {message, code}, fix, next_actions }`.
- For success, return `{ ok: true, result: {valid, errors, warnings}, next_actions }`.

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_validate.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/infralink/cli/validate.py tests/test_cli_validate.py
git commit -m "feat(cli): JSON validate output"
```

### Task 4: JSON output for remaining commands

**Files:**
- Modify: `src/infralink/cli/analyze.py`
- Modify: `src/infralink/cli/check.py`
- Modify: `src/infralink/cli/diagram.py`
- Modify: `src/infralink/cli/docs.py`
- Modify: `src/infralink/cli/resolve.py`
- Modify: `src/infralink/cli/main.py` (info/hosts/edges_list)
- Create: `tests/test_cli_commands_json.py`

**Step 1: Write failing tests**

```python
import json
from click.testing import CliRunner
from infralink.cli.main import cli


def test_info_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["info"])
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_hosts_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["hosts"])
    payload = json.loads(result.output)
    assert payload["ok"] is True


def test_edges_list_json():
    runner = CliRunner()
    result = runner.invoke(cli, ["edges-list"])
    payload = json.loads(result.output)
    assert payload["ok"] is True
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/test_cli_commands_json.py -v`
Expected: FAIL

**Step 3: Implement minimal JSON envelopes for each command**
- Remove Rich usage and tables.
- Return structured data in `result` and contextual `next_actions`.
- For `diagram`/`docs`, return `{ path, format }` in result when files are written.
- For `check`, return per-edge results and summary counts.
- For `resolve`, return resolved edge data (no raw prints), include `fix` on failures.

**Step 4: Run tests to verify they pass**

Run: `pytest tests/test_cli_commands_json.py -v`
Expected: PASS

**Step 5: Commit**

```bash
git add src/infralink/cli/*.py tests/test_cli_commands_json.py
git commit -m "feat(cli): JSON output for all commands"
```

### Task 5: Full test pass

**Step 1: Run full suite**

Run: `pytest -q`
Expected: PASS

**Step 2: Commit (if needed)**

```bash
git add tests
git commit -m "test: update CLI JSON expectations"
```
