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
- Exit `0` for positive results, `1` for negative domain results, `2` for
  usage, `3` for input/schema/entity failures, `4` for provider failures, and
  `70` for unexpected internal failures.
- Safe connection templates containing `secret_ref` placeholders, never
  resolved credentials.
- Offline declared-secret inventory and optional read-only hosted BWS audit.
- Deterministic diagram and documentation artifacts with transactional writes.
- Reproducible wheel/sdist metadata, repository secret scanning, public-data
  boundary checks, and a manually dispatched attested candidate workflow.

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

## Release Candidate Boundary

The candidate workflow is manual and SHA-bound. It checks a clean exact source
commit, runs lint, types, tests, schema determinism, public-data checks, and
checksum-verified Gitleaks before building. The wheel and sdist are built once.
The workflow creates and attests exactly:

```text
infralink-0.2.0-py3-none-any.whl
infralink-0.2.0.tar.gz
manifest.json
SHA256SUMS
```

It uploads a retained GitHub Actions artifact but never tags, publishes, or
deploys. Private consumers verify the artifact ID, provenance, source commit,
and digests before adoption. Rollback restores the previous verified artifact
and digest rather than rebuilding.

## Non-Goals

- Dynamic service discovery
- Deployment or host lifecycle management
- Secret storage, writes, or arbitrary lookup
- Public disclosure of private topology
- Automatic release or production rollout

## Success Criteria

- Every public CLI response validates against its checked-in schema.
- No secret value crosses a serialization boundary.
- Candidate artifacts correspond to one exact, clean source commit.
- Public docs and examples contain only deliberate example topology.
- Compatibility changes have an explicit migration path.

*Document version: 0.2*
