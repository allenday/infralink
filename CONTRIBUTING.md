# Contributing

This repository accepts ordinary code, documentation, schema, and test changes
through GitHub pull requests. Keep changes small enough to review and preserve
the public safety boundaries documented in
[docs/security-boundaries.md](docs/security-boundaries.md).

## Supported Environment

Infralink supports Python 3.10 through 3.12. The Woodpecker quality pipeline
runs all three versions on Linux. Local contributors can use any supported
version for development; use Python 3.12 when you want parity with the release
step.

Create a local environment with a supported interpreter:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
```

On macOS with Homebrew, this is a useful interpreter check:

```bash
/opt/homebrew/bin/python3.12 --version
```

Install optional hosted Bitwarden Secrets Manager audit support only when
working on BWS integration:

```bash
.venv/bin/python -m pip install -e ".[dev,bws]"
```

## Canonical Checks

Run the docs checker whenever Markdown changes:

```bash
.venv/bin/python scripts/check_docs.py
```

Run the full local quality gate before opening a PR:

```bash
.venv/bin/python -m ruff format --check src tests scripts
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m mypy src scripts
.venv/bin/python scripts/generate_cli_schemas.py
.venv/bin/python scripts/generate_observation_schemas.py
.venv/bin/python scripts/generate_release_schemas.py
git diff --exit-code
.venv/bin/python -m pytest
```

The `git diff --exit-code` check matters after schema generation. It proves the
checked-in generated schemas match the current Pydantic contracts.

## Schema Generation

The canonical generators are:

```bash
.venv/bin/python scripts/generate_cli_schemas.py
.venv/bin/python scripts/generate_observation_schemas.py
.venv/bin/python scripts/generate_release_schemas.py
```

Woodpecker runs all three schema generators in every quality job.

## CLI And Examples

Topology commands require explicit sources. Use the sanitized examples for local
checks:

```bash
.venv/bin/infralink --registry examples/registry.yml --edges examples/edges.yml validate
.venv/bin/infralink --registry examples/registry.yml --edges examples/edges.yml info
```

Observation commands use the example observation directory:

```bash
AS_OF=2026-08-17T00:00:00Z
.venv/bin/infralink validate --source examples/observation --as-of "$AS_OF"
.venv/bin/infralink project observation --source examples/observation --as-of "$AS_OF"
```

## Branch And PR Flow

1. Branch from `origin/main`.
2. Keep product behavior, docs, generated schemas, and test changes in the same
   PR when they depend on one another.
3. Do not include local virtualenvs, generated `dist/`, private topology, secret
   values, or Beads/Gas City working state.
4. Run relevant focused checks and the full quality gate when practical.
5. Open a draft PR when the change needs human QA before it is ready.

## Release Flow

Do not publish releases from a development checkout. Woodpecker is the only
release executor, and its release step is manual, `main`-only, and protected by
version, commit, asset, checksum, and signature checks. See
[docs/release-operator-workflow.md](docs/release-operator-workflow.md).
