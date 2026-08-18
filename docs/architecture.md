# Architecture

Infralink is organized around explicit, file-backed infrastructure facts and
bounded command outputs. It does not discover live infrastructure by default and
does not make private provider data public.

## Package Map

- `src/infralink/cli/main.py` owns the root Click command tree, lazy command
  loading, help metadata, parsed command context, and shared envelope emission.
- `src/infralink/cli/contracts.py`, `observation_contracts.py`, and
  `operation_contracts.py` define typed CLI result models used by schema
  generation and tests.
- `src/infralink/core/registry.py`, `edges.py`, and `resolver.py` load declared
  host/service topology, validate typed edges, and resolve safe endpoints.
- `src/infralink/core/template.py` and `src/infralink/secrets/` keep connection
  output at the secret-reference boundary.
- `src/infralink/observation/` loads offline observation documents, canonicalizes
  service/profile/dependency/view data, explains diagnostics, and plans
  readiness or operations views.
- `src/infralink/cli/host_readiness.py`, `host_transport.py`, and
  `host_registry_state.py` support host bootstrap/apply planning and bounded
  operation status.
- `src/infralink/release/contracts.py` defines release candidate, publisher
  request, and attestation contracts for local validation and inspection.
- `src/infralink/generators/` renders deterministic diagram and documentation
  artifacts.

## Data Flow

Topology commands load explicit registry and edge sources from `--registry`,
`--edges`, `INFRALINK_REGISTRY`, and `INFRALINK_EDGES`. Direct operator use may
select one registry checkout root with `registry:` in
`$XDG_CONFIG_HOME/infralink/config.yml`; this selector derives standard
checkout-relative Doctor inputs but never selects a registry revision or
desired state. Examples under `examples/` are demo and test inputs only; they
are not implicit operational fallbacks.

Each CLI invocation emits one structured envelope. Topology commands use
`infralink.cli/v1`; offline observation commands use `agent-cli.response.v1`.
Both include parsed command metadata, bounded results or redacted errors, and
limited next actions.

Generated artifacts are written to explicit output directories and represented
on stdout by bounded artifact metadata. Transactional artifact commands are
currently POSIX/Linux-oriented because they depend on filesystem semantics that
are tested in CI.

## Schemas

Packaged schemas live under `src/infralink/schemas/` and are generated from
typed models:

- CLI schemas: `scripts/generate_cli_schemas.py`
- observation schemas: `scripts/generate_observation_schemas.py`
- release schemas: `scripts/generate_release_schemas.py`

The quality pipeline runs generation and then `git diff --exit-code` so model
and schema drift fails fast.

## Tests

The `tests/` tree covers command contracts, schema generation, public data
boundaries, host operations, release policy, Woodpecker policy, and domain
logic. Policy tests intentionally inspect configuration files such as
`.woodpecker.yml`; update those tests when changing CI or release commands.

## Release Tooling

Woodpecker runs quality jobs for Python 3.10, 3.11, and 3.12. Its release job is
manual, `main`-only, and depends on all quality jobs. Local release helper
scripts validate versions, exact protected-main commits, toolchain checksums,
asset names, checksums, and release attestations. They do not replace the
protected Woodpecker release step.

## More Context

- Observable topology, resources, metrics, and readiness rollups:
  [Observable model](observable-model.md)
- Setup and PR flow: [CONTRIBUTING.md](../CONTRIBUTING.md)
- Security boundaries: [docs/security-boundaries.md](security-boundaries.md)
- v0.2 migration history: [docs/compatibility/v0.2.md](compatibility/v0.2.md)
- Release operator workflow: [docs/release-operator-workflow.md](release-operator-workflow.md)
