# Infralink Product Requirements

Current package version: `0.5.6`.

## Purpose

Infralink makes infrastructure dependencies explicit, reviewable, and safe to
consume by humans, agents, CI systems, and operators. It loads declarative host,
edge, observation, and release data; validates contracts; answers bounded
queries; resolves endpoints without resolving secrets; checks health; and
generates deterministic artifacts.

The public package is provider-neutral at its domain boundary. Optional adapters
may integrate with external providers only through constrained, documented,
read-only or explicitly authorized operations.

## Current Requirements

- Support Python 3.10 through 3.12.
- Validate on Linux/POSIX for artifact-generating commands that rely on
  transactional filesystem semantics.
- Keep UUID-based host identity and typed, validated edges as the topology
  foundation.
- Emit exactly one structured CLI envelope per invocation.
- Preserve stable `infralink.cli/v1` and `agent-cli.response.v1` result/error
  contracts, bounded collections, opaque continuation cursors, and actionable
  next steps.
- Preserve stable exit codes for domain results, contract failures, platform
  support, artifact I/O, and unexpected failures.
- Return safe connection templates containing declared `secret_ref`
  placeholders, never resolved credentials.
- Provide offline declared-secret inventory and optional read-only hosted BWS
  metadata audit.
- Provide offline observation documents, diagnostics, service/profile operations
  views, readiness suites, and packaged observation schemas.
- Provide local host bootstrap/apply planning, status, logs, and verifier
  surfaces with bounded sanitized output and explicit operator authority for
  mutations.
- Provide release candidate, publisher request, and attestation validation
  surfaces without turning local commands into a publisher.
- Generate deterministic diagram, documentation, CLI schema, observation schema,
  and release schema artifacts.
- Protect public examples and docs from private topology or secret leakage.
- Keep Woodpecker as the only release executor.

## CLI Surface

The command tree includes:

```text
infralink
|-- help [command ...]
|-- version
|-- info
|-- hosts
|-- host create|list|show|bootstrap|verifier|apply|status|logs
|-- services
|-- service list|show
|-- edges-list
|-- edge list|show
|-- app list|show
|-- validate
|-- check
|-- resolve <edge-id>
|-- --registry <checkout-root> analyze --output <directory>
|-- diagram --output <directory>
|-- docs --output <directory>
|-- secrets inspect|audit
|-- capabilities
|-- project observation|secrets|view|readiness
|-- explain <code>
|-- release inspect|validate-candidate|render-publisher-request|inspect-attestation
`-- registry host get|patch
```

Topology commands require declared sources through flags or environment
variables. Packaged examples are explicit demo and test inputs only.

## Exit Codes

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

## Secret Boundary

Topology stores references such as `example/database-password`, not values.
`resolve` may return:

```text
postgresql://app:${secret:example/database-password}@192.0.2.10:5432/app
```

`secrets inspect` reports declared references and source locations only.
`secrets audit --provider bws` may inspect hosted Bitwarden metadata for those
declared references, but cannot accept arbitrary secret IDs and never retrieves
values. Production audit requires `BWS_ACCESS_TOKEN` and `BWS_ORGANIZATION_ID`.
Custom BWS endpoints remain out of scope until explicitly designed.

## Release Boundary

Woodpecker is the only CI release executor. Its manual release step runs for
`main` on Python 3.12 only after all three Python-version quality gates succeed.
It requires the requested version to equal the package version and requires the
pipeline commit to equal the current `main` commit. It publishes exactly:

```text
infralink-<version>-py3-none-any.whl
infralink-<version>.tar.gz
SHA256SUMS
SHA256SUMS.sigstore.json
```

GitHub is the public source and release destination. The step fails if the tag
or release already exists, builds the packages, checks them with Twine, writes
canonical checksums, and signs the checksum file with Cosign. Local release
scripts are validators and asset assemblers; they are not an alternate release
path.

## Non-Goals

- Dynamic service discovery.
- Secret storage, writes, resolved credential output, or arbitrary secret lookup.
- Public disclosure of private topology.
- Automatic release, non-main publication, or production rollout.
- Running live host or provider mutations without explicit operator authority.

## Success Criteria

- Every public CLI response validates against its checked-in schema.
- No secret value crosses a serialization boundary.
- Generated schemas are deterministic and clean after regeneration.
- Release assets correspond to the exact protected `main` source commit.
- Public docs and examples contain only deliberate example topology.
- Compatibility changes have an explicit migration path.

## Historical Context

The `v0.2` foundation remains documented for migration and release-history
purposes. Keep that history in [docs/compatibility/v0.2.md](docs/compatibility/v0.2.md)
and [docs/releases/](docs/releases/) rather than using it as the current
top-level product frame.
