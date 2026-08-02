# Woodpecker CI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add and exercise a secret-free Woodpecker quality pipeline for Infralink.

**Architecture:** A single root `.woodpecker.yml` uses a Python-version matrix and
executes the repository's existing quality, test, schema, build, and metadata gates.
A focused policy test treats the CI file as an untrusted deployment boundary and
rejects secret, privilege, publication, or deployment capabilities.

**Tech Stack:** Woodpecker CI 3.x YAML, Python 3.10-3.12, PyYAML, pytest, Ruff,
mypy, build, Twine.

---

### Task 1: Define the workflow policy

**Files:**
- Create: `tests/test_woodpecker_ci_policy.py`

- [ ] **Step 1: Write a failing policy test**

Parse `.woodpecker.yml` with `yaml.safe_load`. Assert:

```python
assert workflow["when"] == [
    {"event": "push"},
    {"event": "pull_request"},
    {"event": "manual"},
]
assert workflow["matrix"]["PYTHON_VERSION"] == ["3.10", "3.11", "3.12"]
```

Require commands for editable development installation, Ruff format/lint, mypy,
pytest, schema generation and clean-tree checks, build, and Twine. Recursively reject
the keys `secrets`, `privileged`, `volumes`, `services`, and `depends_on`, and reject
commands containing tag, push, upload, publish, deploy, release, registry login, or
remote mutation operations.

- [ ] **Step 2: Run the test and verify RED**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_woodpecker_ci_policy.py -q --no-cov
```

Expected: failure because `.woodpecker.yml` does not exist.

### Task 2: Add the matrix pipeline

**Files:**
- Create: `.woodpecker.yml`

- [ ] **Step 1: Add the workflow**

Use this structure:

```yaml
when:
  - event: push
  - event: pull_request
  - event: manual

matrix:
  PYTHON_VERSION:
    - "3.10"
    - "3.11"
    - "3.12"

steps:
  quality:
    image: python:${PYTHON_VERSION}-slim-bookworm
    commands:
      - python -m pip install --disable-pip-version-check -e ".[dev]"
      - python -m ruff format --check src tests scripts
      - python -m ruff check src tests scripts
      - python -m mypy src scripts
      - python -m pytest
      - python scripts/generate_cli_schemas.py
      - git diff --exit-code
      - test -z "$(git ls-files --others --exclude-standard src/infralink/schemas)"
      - python -m build
      - python -m twine check dist/*
```

Install `git` in the container before the clean-tree assertions if the selected
Python image does not provide it.

- [ ] **Step 2: Run policy and repository gates**

Run:

```bash
/tmp/infralink-plan-venv/bin/python -m pytest \
  tests/test_woodpecker_ci_policy.py -q --no-cov
/tmp/infralink-plan-venv/bin/ruff format --check src tests scripts
/tmp/infralink-plan-venv/bin/ruff check src tests scripts
/tmp/infralink-plan-venv/bin/mypy src scripts
/tmp/infralink-plan-venv/bin/python -m pytest
```

Expected: all commands pass.

- [ ] **Step 3: Lint with Woodpecker CLI**

Run the pinned Woodpecker 3.15 CLI linter against `.woodpecker.yml`.

Expected: no linter errors or deprecation warnings.

- [ ] **Step 4: Commit**

```bash
git add .woodpecker.yml tests/test_woodpecker_ci_policy.py
git commit -m "ci: add woodpecker quality matrix"
```

### Task 3: Publish and inspect the CI result

**Files:**
- No additional files.

- [ ] **Step 1: Push the feature branch**

```bash
git push -u origin feat/infralink-v0.2-foundation
```

Expected: the exact local HEAD is present on the remote feature branch.

- [ ] **Step 2: Inspect Woodpecker**

Use the Woodpecker MCP to list repositories, find `cyberstorm-dev/infralink`, and
inspect the pipeline for the pushed commit.

Expected: three successful matrix workflows, one per supported Python version.

- [ ] **Step 3: Diagnose failures without operational side effects**

If a workflow fails, read its step logs through the MCP, make a scoped test-driven
fix, independently review it, and push again. Do not add secrets, privileges,
publication, tagging, release, or deployment behavior.
