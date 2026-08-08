# Infralink

Infralink is a Python library and agent-oriented CLI for modeling infrastructure
topology with UUID-based hosts, typed edges, bounded queries, health checks,
safe connection templates, diagrams, and generated documentation.

## Installation

Infralink supports Python 3.10 through 3.12. Artifact-generating commands require POSIX
filesystem semantics and currently support Linux for secure transactional writes.

```bash
python -m pip install infralink
python -m pip install "infralink[bws]"  # optional hosted Bitwarden Secrets Manager audit
```

## Public Example

```yaml
# registry.yml
hosts:
  d1b9e5d5-36b0-459d-a556-96622811fbd5:
    canonical_name: database.example.com
    status: active
    group: production
    cloud: example-cloud
    tailscale_ip: 192.0.2.10
    services:
      postgresql:
        port: 5432
        protocol: postgresql
        exposure: internal
```

```yaml
# edges.yml
schema_version: "1.0"
edges:
  - id: 058e29ff-57b9-47c8-b6fa-0914ac03e25c
    type: database
    from:
      hosts: [fa2b9872-d94c-4b20-a73a-57a205560769]
      service: api
    to:
      host: d1b9e5d5-36b0-459d-a556-96622811fbd5
      service: postgresql
      port: 5432
    protocol: postgresql
    auth:
      type: password
      secret_ref: example/database-password
```

## CLI Contract

Every invocation writes exactly one `infralink.cli/v1` JSON envelope to stdout.
The envelope includes `ok`, a shallow parsed command view, a typed `result` or
redacted `error`, and bounded `next_actions`. Lists use explicit limits and
opaque cursors. Use `infralink help [command ...]` for machine-readable
discovery.

Legacy topology commands retain their `infralink.cli/v1` JSON contract. Offline
observation commands use `agent-cli.response.v1`, default to YAML, and accept
explicit `--output json` or `--output yaml`:

```bash
infralink capabilities
infralink validate --source examples/observation --as-of "$AS_OF"
infralink project observation --source examples/observation --as-of "$AS_OF"
infralink project secrets --source examples/observation --as-of "$AS_OF"
infralink project view service-overview --source examples/observation --as-of "$AS_OF"
infralink project readiness ci-release --source examples/observation --as-of "$AS_OF"
infralink explain schema-version-unsupported
```

Observation documents declare `schema_version: infralink.observation/v1` and
may be validated against the packaged schemas under
`infralink/schemas/observation/v1`. The examples declare service, dependency,
and view signals in the `service/...`, `dependency/...`, and `view/...`
namespaces. Planning is deliberately offline: provider, renderer, datasource,
observation backend, and secret backend identifiers remain opaque. Public
outputs contain aliases and binding metadata, never secret values or private
provider data.

```bash
infralink --registry registry.yml --edges edges.yml validate
infralink --registry registry.yml --edges edges.yml host show \
  d1b9e5d5-36b0-459d-a556-96622811fbd5
infralink --registry registry.yml --edges edges.yml resolve \
  058e29ff-57b9-47c8-b6fa-0914ac03e25c --user app --database app
infralink --registry registry.yml --edges edges.yml secrets inspect
```

Resolution returns endpoint metadata, declared secret references, and a safe
template such as:

```text
postgresql://app:${secret:example/database-password}@192.0.2.10:5432/app
```

The CLI never returns resolved secret values and accepts no arbitrary secret
identifier lookup.

Exit codes are stable:

| Code | Meaning |
| --- | --- |
| `0` | Positive domain result |
| `1` | Completed negative domain result |
| `2` | Usage error |
| `3` | Input, schema, or entity error |
| `4` | Provider or authentication failure |
| `69` | Unsupported platform |
| `70` | Unexpected internal failure |
| `74` | Artifact I/O failure or retained recovery state |

Exit `74` uses `artifact_io_failed` for storage failures and
`artifact_recovery_required` when recovery state is retained. `internal_error`
is reserved for exit `70`.

## Python API

```python
from infralink import EdgeResolver, EdgeSet, Registry

registry = Registry.load("registry.yml")
edges = EdgeSet.load("edges.yml")
resolver = EdgeResolver(registry, edges)

endpoint = resolver.get_target_endpoint("058e29ff-57b9-47c8-b6fa-0914ac03e25c")
template = resolver.get_connection_template(
    "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
    user="app",
    database="app",
)
```

Legacy Python URL helpers remain available for compatibility but are
deprecated. New integrations should use secret references and connection
templates.

## Hosted BWS

`infralink secrets inspect` is offline. Provider audit requires the optional
extra and hosted Bitwarden Secrets Manager credentials:

```bash
export BWS_ACCESS_TOKEN="<machine-account-token>"
export BWS_ORGANIZATION_ID="<organization-uuid>"
infralink --registry registry.yml --edges edges.yml secrets audit --provider bws
```

Production `v0.3.0` accepts Bitwarden's hosted endpoints only. Endpoint override
environment variables are rejected; custom endpoints remain deferred. Audit is
read-only, is restricted to references declared by topology, and does not fetch
secret values.

## Release Adoption And Rollback

The manual Woodpecker release step runs only for `main` on Python 3.12 after
all three parallel Python-version quality gates. It requires release version
`0.3.0` and the pipeline commit to equal the current `main` commit, then
rebuilds and publishes exactly the wheel, sdist, `SHA256SUMS`, and
`SHA256SUMS.sigstore.json` to GitHub Release `v0.3.0`. Existing tags or releases
stop the process for operator inspection.

Consumers verify the Cosign bundle and `SHA256SUMS` before installing the
wheel, and record the source commit and wheel digest in consumer configuration.
Rollback restores the previously verified release revision and digest; it does
not rebuild old source or mutate the existing public release.

## Development

```bash
python -m pip install -e ".[dev]"
ruff format --check src tests scripts
ruff check src tests scripts
mypy src scripts
python -m pytest
```

See [the v0.2 compatibility guide](docs/compatibility/v0.2.md), [PRD](PRD.md),
and [backlog](BACKLOG.md).

## License

MIT
