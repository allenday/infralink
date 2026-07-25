# Infralink

Infralink is a Python library and JSON-only CLI for modeling infrastructure
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
| `70` | Unexpected internal failure |

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

Production `v0.2.0` accepts Bitwarden's hosted endpoints only. Endpoint override
environment variables are rejected; custom endpoints remain deferred. Audit is
read-only, is restricted to references declared by topology, and does not fetch
secret values.

## Candidate Adoption And Rollback

The manual GitHub `Release candidate` workflow requires a full source commit
SHA and the same selected workflow ref. It runs all gates, builds one wheel and
one sdist once, creates canonical `manifest.json` and `SHA256SUMS`, attests the
four files, and uploads them as an Actions artifact. It does not publish,
release, tag, or deploy.

Secret scanning deliberately uses the pinned Gitleaks CLI archive with its
published checksum rather than `gitleaks/gitleaks-action`; this keeps the public
candidate workflow free of a long-lived organization license secret.

Consumers adopt the exact artifact ID after checking the GitHub attestation,
source commit, manifest, and checksums. Pin that artifact's wheel digest in the
private consumer gate. Rollback means restoring the previously verified
artifact ID, source commit, and wheel digest; no rebuild is involved.

Repository administrators must enable GitHub artifact attestations and retain
candidate artifacts long enough for the private verification gate.

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
