# Infralink Product Requirements

## Purpose

Infralink makes infrastructure dependencies explicit and reviewable. It loads
declarative host and edge data, validates it, answers bounded topology queries,
resolves endpoints, checks health, and generates deterministic artifacts.

The primary users are infrastructure engineers, automated agents, CI systems,
and on-call operators. The public package is provider-neutral at its domain
boundary; optional adapters may integrate with external providers.

## v0.2 Requirements

- Python 3.10 through 3.12 on supported POSIX/Linux systems.
- UUID-based host identity and typed, validated edges.
- Stable `infralink.cli/v1` JSON envelopes for every CLI outcome.
- Shallow parsed command metadata, typed results, stable errors, bounded
  collections, opaque continuation cursors, and actionable next steps.
- Stable exit codes for domain results, contract failures, platform support,
  artifact I/O, and unexpected failures.
- Safe connection templates containing `secret_ref` placeholders, never
  resolved credentials.
- Offline declared-secret inventory and optional read-only hosted BWS audit.
- Deterministic diagram and documentation artifacts with transactional writes.
- Reproducible wheel/sdist metadata, repository secret scanning, public-data
  boundary checks, and a protected manual Woodpecker release.

The stable exit-code contract is:

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

## CLI Surface

```text
infralink
|-- help [command ...]
|-- version
|-- info
|-- hosts
|-- host show <host-id>
|-- services
|-- service show <service-id>
|-- edges-list
|-- edge show <edge-id>
|-- app list
|-- app show <app-id>
|-- validate
|-- check
|-- resolve <edge-id>
|-- analyze --output <directory>
|-- diagram --output <directory>
|-- docs --output <directory>
`-- secrets
    |-- inspect
    `-- audit --provider bws
```

All output is one JSON document. Long collections require limits and cursors;
generated content is written to explicit output directories and represented by
bounded artifact metadata in stdout.

## Secret Boundary

Topology stores references such as `example/database-password`, not values.
`resolve` can return:

```text
postgresql://app:${secret:example/database-password}@192.0.2.10:5432/app
```

`secrets inspect` reports only declared references and source locations.
`secrets audit --provider bws` may inspect provider metadata for those declared
references, but cannot accept an arbitrary secret ID and never retrieves a
value. Production requires `BWS_ACCESS_TOKEN` and `BWS_ORGANIZATION_ID` and uses
hosted Bitwarden endpoints only. Custom endpoints are deferred beyond `v0.2.0`.

## Release Boundary

Woodpecker is the only CI and release executor. Its manual release step runs
for `main` on Python 3.12 only after the three-version quality matrix succeeds.
It requires the requested version and package version to equal `0.2.0`, and
requires the pipeline commit to equal the current `main` commit. It publishes
exactly:

```text
infralink-0.2.0-py3-none-any.whl
infralink-0.2.0.tar.gz
SHA256SUMS
SHA256SUMS.sigstore.json
```

GitHub is only the public source and release destination. The step fails if the
tag or release already exists, builds the packages, checks them with Twine,
writes canonical checksums, and signs the checksum file with Cosign. Private
compatibility checks are optional diagnostics and do not control publication.

## Non-Goals

- Dynamic service discovery
- Deployment or host lifecycle management
- Secret storage, writes, or arbitrary lookup
- Public disclosure of private topology
- Automatic release or production rollout

## Success Criteria

- Every public CLI response validates against its checked-in schema.
- No secret value crosses a serialization boundary.
- Release assets correspond to the exact protected `main` source commit.
- Public docs and examples contain only deliberate example topology.
- Compatibility changes have an explicit migration path.

*Document version: 0.2*
