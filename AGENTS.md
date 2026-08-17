# AGENTS.md

These instructions apply repo-wide. Human-facing product context lives in
[README.md](README.md); contribution flow lives in [CONTRIBUTING.md](CONTRIBUTING.md).

## Project Snapshot

Infralink is a Python package and CLI for declarative infrastructure topology,
offline observation contracts, safe connection templates, generated artifacts,
host-operation planning, and release evidence inspection. Current package
version is `0.5.6`.

## Layout

- `src/infralink/cli/`: Click command tree and structured output envelopes.
- `src/infralink/core/`: registry, edge, resolver, schema, and template domain logic.
- `src/infralink/observation/`: offline observation models, loading, diagnostics, and planning.
- `src/infralink/release/`: release candidate, publisher request, and attestation contracts.
- `src/infralink/schemas/`: generated packaged JSON Schemas.
- `examples/`: sanitized public examples only.
- `tests/`: pytest coverage, including CI, release, schema, and public-data policy tests.
- `docs/`: architecture, security, compatibility, release, and historical planning notes.

## Setup

Use Python 3.10, 3.11, or 3.12. Do not use the macOS system Python 3.9.

```bash
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

## Checks

Run focused checks for the files you change, then run the full gate before a PR:

```bash
.venv/bin/python scripts/check_docs.py
.venv/bin/python -m ruff format --check src tests scripts
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m mypy src scripts
.venv/bin/python scripts/generate_cli_schemas.py
.venv/bin/python scripts/generate_observation_schemas.py
.venv/bin/python scripts/generate_release_schemas.py
git diff --exit-code
.venv/bin/python -m pytest
```

Schema generation is deterministic. If a schema-generating command changes
tracked files, either commit the generated schema updates with the matching code
change or fix the generator/model drift.

## Security Boundaries

- Public docs and examples must stay sanitized. Use documentation IP ranges,
  placeholder hosts, and declared secret references only.
- Never print, resolve, or invent secret values. `secrets inspect` is offline;
  hosted BWS audit is read-only and limited to declared references.
- Do not run host `apply`, bootstrap `--apply`, live provider actions, tag
  creation, release publication, or merge operations during ordinary coding tasks.
- Woodpecker is the release executor. Draft PRs are acceptable; releases and
  merges require explicit human action.

See [docs/security-boundaries.md](docs/security-boundaries.md) for details.

## Change Guidelines

- Preserve the `infralink.cli/v1` and `agent-cli.response.v1` envelope contracts
  unless the task explicitly changes public API.
- Keep docs link-oriented. Avoid duplicating long command lists across files;
  update the canonical source and link to it.
- Add or update tests for behavior, schema, CI policy, release policy, and docs
  validation when those surfaces change.
- Keep historical docs useful, but make current docs explicit about the current
  `0.5.6` state.

## PR Expectations

Before opening a PR, verify the intended files only, run the relevant checks,
summarize validation in the PR body, and open a draft PR when human QA is still
the checkpoint.
