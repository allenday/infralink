# Woodpecker CI Design

## Goal

Add a secret-free, nondeploying Woodpecker pipeline that gives Infralink an
independent CI signal on pushes, pull requests, and manual runs.

## Considered Approaches

1. Mirror every GitHub workflow, including candidate attestation and promotion.
   This provides maximum parity but would duplicate trusted release machinery and
   require secrets. It is outside the requested clean-build scope.
2. Run one Python version with the core test suite. This is fast, but it would not
   validate the published Python 3.10-3.12 compatibility contract.
3. Run the complete quality and packaging gates as a Python 3.10-3.12 matrix. This
   is the selected approach because it is independently useful, requires no
   secrets, and exercises the supported interpreter range.

## Workflow

The repository root contains one `.woodpecker.yml`. It runs for:

- every push;
- every pull request; and
- explicit manual runs.

The workflow matrix uses Python 3.10, 3.11, and 3.12 container images. Each matrix
entry installs the development dependencies and runs:

1. Ruff formatting and lint checks;
2. strict mypy checks;
3. the complete pytest suite with branch coverage;
4. deterministic CLI schema generation with a clean-tree assertion;
5. one wheel and sdist build; and
6. Twine metadata validation.

Repeating quality and packaging gates across all three interpreters is intentional:
it keeps each matrix workflow self-contained and avoids cross-workflow artifacts or
plugins.

## Security Boundary

The workflow declares no secrets, privileged mode, host mounts, service containers,
plugins, registry credentials, deployment steps, or publication commands. It does
not create tags, GitHub releases, OCI artifacts, PyPI uploads, or fleet mutations.

## Validation

A policy test parses `.woodpecker.yml` and proves the trigger set, matrix versions,
required commands, and forbidden capabilities. The workflow is also linted with the
pinned Woodpecker CLI before it is pushed.

After push, the Woodpecker MCP is used to identify the repository and inspect the
resulting pipeline and step logs. MCP/API authentication is an operational
prerequisite, not a reason to weaken or embed credentials in the workflow.
