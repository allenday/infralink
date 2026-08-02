# Infralink v0.2 Foundation Design

**Date:** 2026-07-25

**Status:** Approved for implementation planning

## Purpose

Infralink `v0.2.0` establishes a production-ready foundation for read-only
infrastructure topology inspection. It stabilizes the existing Python API and
command-line interface, defines a safe secret-resolution boundary, and adds
release gates that allow a private infrastructure consumer to validate the
exact public package artifact before adoption.

This release does not deploy infrastructure or mutate registry data.

## Context

The infrastructure control plane uses separate authoritative repositories:

- Public software and reusable infrastructure logic are maintained on GitHub.
- Sensitive fleet topology is maintained in a private, self-hosted Gitea
  repository.
- A private infrastructure-management repository consumes Infralink and
  orchestrates validation and deployment through Woodpecker.

This separation is intentional. Infralink must remain safe to publish and test
without access to private fleet data. Private compatibility is proven in
Woodpecker using the built package artifact.

The implementation will harden the current codebase in place. A broad rewrite,
service relocation, and control-plane consolidation are outside this release.

## Architecture

Infralink remains a Python library with a thin Click CLI. Its responsibilities
are separated into four explicit boundaries.

### Domain

The domain layer owns registry, node, service, edge, health-check, and
secret-reference models. It performs no network access and has no dependency on
Bitwarden or other service providers.

### Application

The application layer implements read-only operations:

- load topology
- inspect hosts, services, applications, and edges
- validate schemas and cross-object references
- resolve topology endpoints and safe connection templates
- check declared edge health
- inspect and audit declared secret references

Application operations return typed results that can be used identically by the
Python API, CLI, and future protocol adapters.

### Adapters

The base package includes filesystem and YAML adapters. Hosted Bitwarden
Secrets Manager support is provided by the optional `infralink[bws]` extra.

The BWS adapter implements a vendor-neutral secret resolver contract. Provider
objects and errors are normalized before crossing into the application layer.

### Interfaces

The supported interfaces in `v0.2.0` are:

- the public Python API
- a JSON-only command-line interface

A future MCP server will call application operations directly. It will not
invoke the CLI as a subprocess or duplicate validation, provider, or
authorization logic.

## Read-Only Boundary

The initial stable contract permits inspection, validation, topology
resolution, health checking, and secret-reference auditing.

It does not permit:

- registry writes
- secret writes
- infrastructure deployment
- service restart or shutdown
- repository mutation
- automated remediation

Commands that generate diagrams or documentation may write only to an
explicitly selected local output path. They do not mutate topology or a remote
system.

## CLI Purpose And Users

The CLI provides deterministic, machine-readable topology work product for:

- operators investigating infrastructure state
- CI jobs enforcing topology contracts
- agents discovering and following safe inspection workflows
- reference consumers integrating the Infralink package

Human-readable rendering is not part of the `v0.2.0` command contract.

## Command Tree

```text
infralink
|-- help [command]
|-- version
|-- info
|-- hosts
|-- host show <host-id>
|-- services
|-- edges-list
|-- edge show <edge-id>
|-- service show <service-id>
|-- validate
|-- resolve <edge-id>
|-- check
|-- app
|   |-- list
|   `-- show <app-id>
|-- analyze
|-- diagram
|-- docs
`-- secrets
    |-- inspect
    `-- audit
```

Existing commands remain available unless they violate the secret-output
boundary. Before behavior changes, characterization tests will capture current
entry points, imports, envelopes, and exit codes.

## Machine-Readable Command Contract

Every invocation emits exactly one JSON document to standard output. The
envelope is versioned independently from the package:

```json
{
  "schema_version": "infralink.cli/v1",
  "ok": true,
  "command": {
    "raw": "infralink validate",
    "parsed": {
      "path": ["validate"],
      "args": {},
      "flags": []
    },
    "resolved": {
      "version": "0.2.0",
      "cwd": "/work",
      "registry": "registry.yml",
      "edges": "edges.yml"
    }
  },
  "result": {
    "valid": true,
    "errors": {
      "items": [],
      "page": {
        "limit": 100,
        "returned": 0,
        "total": 0,
        "next_cursor": null
      }
    },
    "warnings": {
      "items": [],
      "page": {
        "limit": 100,
        "returned": 0,
        "total": 0,
        "next_cursor": null
      }
    },
    "summary": {
      "error_count": 0,
      "warning_count": 0
    }
  },
  "next_actions": [
    {
      "rel": "check",
      "argv": ["infralink", "check"],
      "command": "infralink check",
      "description": "Check declared edge health",
      "safe": true
    }
  ],
  "meta": {
    "truncated": false
  }
}
```

The parsed command view stays close to parser truth. It identifies the command
path, bound arguments, options, and flags without exposing Click internals or
business interpretation.

The resolved view records execution context that can otherwise cause ambiguity:

- executable and Infralink version
- current working directory
- selected registry and edges sources
- selected provider, when applicable

`command.raw` is a canonical, shell-escaped rendering of the received argument
vector. It is not the literal original shell input because Python cannot recover
the caller's original quoting. Sensitive arguments are redacted before entering
`command.raw`,
`command.parsed`, errors, logs, or test diagnostics.

### Command Result Contracts

The envelope is common, but each command also has a minimum stable result
contract:

| Command | Required result fields |
| --- | --- |
| root | `version`, `commands` |
| `help` | `path`, `description`, `arguments`, `options`, `examples` |
| `version` | `version`, `cli_schema_version` |
| `info` | `sources`, `summary` |
| `hosts` | `items`, `page` |
| `host show` | `host`, `services`, `projects` |
| `services` | `items`, `page` |
| `edges-list` | `items`, `page` |
| `edge show` | `edge`, `secret_refs` |
| `service show` | `service`, `hosts`, `ports`, `protocols` |
| `validate` | `valid`, `errors`, `warnings`, `summary` |
| `resolve` | `edge`, `endpoint`, `connection_template`, `secret_refs` |
| `check` | `healthy`, `checks`, `summary` |
| `app list` | `items`, `page` |
| `app show` | `app`, `services`, `edges` |
| `analyze` | `analysis`, `artifacts` |
| `diagram` | `artifacts`, `summary` |
| `docs` | `artifacts`, `summary` |
| `secrets inspect` | `references`, `locations`, `summary` |
| `secrets audit` | `provider`, `references`, `summary` |

Normative JSON Schemas for the envelope, shared types, and every command result
ship as package resources under `infralink/schemas/cli/v1/`. CI validates every
CLI response and documentation example against those schemas.

The schemas use these shared types:

| Type | Required fields and types |
| --- | --- |
| `Page[T]` | `items: array[T]`, `page: PageInfo` |
| `PageInfo` | `limit: integer`, `returned: integer`, `total: integer or null`, `next_cursor: string or null` |
| `Diagnostic` | `code: string`, `path: string or null`, `message: string`, `severity: "error" or "warning"` |
| `HostSummary` | `id: string`, `canonical_name: string`, `status: string`, `service_count: integer`, `services: array[string] maxItems 128`, `services_truncated: boolean`, `project_count: integer`, `projects: array[string] maxItems 64`, `projects_truncated: boolean` |
| `EdgeSummary` | `id: string`, `type: string`, `from: object`, `to: object`, `protocol: string or null`, `secret_ref_count: integer`, `secret_refs: array[string] maxItems 32`, `secret_refs_truncated: boolean` |
| `Endpoint` | `host: string`, `port: integer`, `protocol: string or null` |
| `CheckResult` | `edge_id: string`, `healthy: boolean`, `status: string`, `latency_ms: number or null`, `error_code: string or null` |
| `AppSummary` | `id: string`, `service_count: integer`, `edge_count: integer` |
| `ServiceSummary` | `id: string`, `host_count: integer`, `host_ids: array[string] maxItems 128`, `hosts_truncated: boolean`, `port_count: integer`, `ports: array[integer] maxItems 64`, `ports_truncated: boolean`, `protocol_count: integer`, `protocols: array[string] maxItems 32`, `protocols_truncated: boolean` |
| `SourceLocation` | `source: string`, `path: string` |
| `SecretReferenceStatus` | `ref: string`, `location_count: integer`, `location_preview: array[SourceLocation] maxItems 16`, `locations_truncated: boolean`, `project: string or null`, `present: boolean or null`, `accessible: boolean or null`, `error_code: string or null` |
| `Artifact` | `path: string`, `media_type: string`, `sha256: string` |

Command fields compose those types as follows:

- `hosts.items` is `array[HostSummary]`.
- `host show.host` is `HostSummary`; `services` and `projects` are
  `Page[string]`.
- `services.items` is `array[ServiceSummary]`.
- `edges-list.items` is `array[EdgeSummary]`.
- `edge show.edge` is `EdgeSummary`; `secret_refs` is `Page[string]`.
- `service show.service` is `ServiceSummary`; `hosts` is `Page[string]`,
  `ports` is `Page[integer]`, and `protocols` is `Page[string]`.
- `validate.errors` and `validate.warnings` are `Page[Diagnostic]`;
  `summary` contains integer `error_count` and `warning_count`.
- `resolve.edge` is `EdgeSummary`, `endpoint` is `Endpoint`,
  `connection_template` is `string or null`, and `secret_refs` is
  `Page[string]`.
- `check.checks` is `Page[CheckResult]`; `summary` contains integer `total`,
  `healthy`, and `unhealthy` counts.
- `app list.items` is `array[AppSummary]`.
- `app show.app` is `AppSummary`; `services` is `Page[ServiceSummary]` and
  `edges` is `Page[EdgeSummary]`.
- `analyze.analysis` contains integer host, service, and edge counts plus
  `diagnostics: Page[Diagnostic]`; `artifacts` is `Page[Artifact]`.
- `diagram.artifacts` and `docs.artifacts` are `Page[Artifact]`; each summary
  contains integer `artifact_count`.
- `secrets inspect.references` and `secrets audit.references` are
  `Page[SecretReferenceStatus]`. `secrets inspect.locations` is
  `Page[SourceLocation]` and is populated when `--ref <declared-ref>` selects
  one declared reference. Each summary contains integer `total`, `present`,
  `missing`, `accessible`, and `denied` counts, using zero for categories that
  do not apply to offline inspection.

Root, help, version, and info schemas define their fixed descriptor and source
objects directly. Adding optional fields is compatible; removing or redefining
required fields requires a new envelope version.

## HATEOAS-Style Affordances

Every success and error response contains contextual `next_actions`. Each
action includes:

- `rel`: stable relationship name
- `argv`: structured argument vector
- `command`: canonical shell-escaped rendering provided for convenience
- `description`: concise purpose
- `safe`: whether the action is read-only
- `templated`: present and true when placeholders require binding
- `bindings`: required names, types, and allowed or suggested values for a
  templated action

For example:

```json
{
  "rel": "resolve",
  "argv": ["infralink", "resolve", "{edge_id}"],
  "command": "infralink resolve '{edge_id}'",
  "description": "Resolve a selected edge",
  "safe": true,
  "templated": true,
  "bindings": {
    "edge_id": {
      "type": "string",
      "required": true,
      "source": "result.items[].id"
    }
  }
}
```

Consumers bind values into `argv`; they do not construct or execute shell
strings from topology data.

Root invocation returns the command tree and entry-point actions. Command help
returns arguments, flags, output shape, and examples in the same envelope.
`--help` and `--version` are normalized to JSON rather than bypassing the
contract with Click's default prose.

## Error Contract

Execution failures replace `result` with:

```json
{
  "error": {
    "code": "stable_error_code",
    "message": "A concise redacted explanation",
    "details": {}
  },
  "fix": "A concrete repair instruction"
}
```

Provider and parser internals are never used as public error codes. Details are
optional and must be safe to serialize.

Exit codes are:

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

A negative domain result is not an execution failure. The defined negative
domain outcomes are:

- `validate`: `result.valid` is false
- `check`: `result.healthy` is false
- `secrets audit`: one or more required references are missing or inaccessible

These return `ok: true` and exit code `1`. Loading, usage, provider, and internal
failures return `ok: false` with their assigned nonzero exit codes.

Identifier lookup failure for `host show`, `edge show`, `service show`,
`app show`, or `resolve` returns:

- `ok: false`
- exit code `3`
- error code `entity_not_found`
- redacted details containing `entity_type` and `requested_id`
- a `fix` directing the caller to the corresponding discovery command
- a safe next action to `hosts`, `services`, `edges-list`, or `app list`

Not-found is an input failure, not a negative domain result.

## Output-Size Policy

Every potentially unbounded collection uses `Page[T]`, including nested
validation diagnostics, health checks, application services and edges, secret
references, analysis diagnostics, and generated artifacts. Each page returns
at most 100 items by default and accepts an explicit limit up to 1,000.

Summary arrays embedded in `HostSummary`, `EdgeSummary`, and `ServiceSummary`
are not authoritative collections. Their schemas enforce the stated
`maxItems`, expose total counts and truncation flags, and provide filtered
`host show`, `edge show`, or `service show` next actions for complete pageable
membership. `SecretReferenceStatus.location_preview` similarly escalates to
`secrets inspect --ref <declared-ref> --collection locations`. A response that
exceeds a summary bound is truncated, not rejected.

When any collection has more results, its `page.next_cursor` is non-null, the
envelope sets `meta.truncated: true`, and `next_actions` includes a continuation
action identifying both the command and collection being continued. Cursors are
opaque and bound to the command, selected sources, filters, and collection name.
They cannot be reused to change any of those inputs.

All commands with pageable output accept `--limit` and `--cursor`. Commands with
more than one `Page[T]` field also require `--collection <field>` whenever a
cursor is supplied. A continuation action therefore uses structured arguments
such as:

```json
{
  "rel": "continue",
  "argv": [
    "infralink",
    "validate",
    "--collection",
    "errors",
    "--cursor",
    "{cursor}",
    "--limit",
    "100"
  ],
  "command": "infralink validate --collection errors --cursor '{cursor}' --limit 100",
  "description": "Continue validation errors",
  "safe": true,
  "templated": true,
  "bindings": {
    "cursor": {
      "type": "string",
      "required": true,
      "source": "result.errors.page.next_cursor"
    }
  }
}
```

On a continuation request, the selected collection advances from its cursor.
Other paged fields deterministically return their first page, preserving the
command result schema. A cursor supplied without the required collection, or
for a different collection or input set, returns a usage error.

Health checks are recomputed on every `check` invocation; a cursor is not a
snapshot of prior observations. The cursor selects a stable, sorted edge
collection and is bound to topology sources, selected edge IDs, filters, and
timeout, but not to volatile latency or health outcomes.

Root command descriptors and fixed summaries are schema-bounded and do not
paginate. Nested detail is summarized by default and expanded only through an
explicit option or follow-up command. Generated documents and diagrams require
explicit output paths. Log retrieval is outside the `v0.2.0` scope.

## Safe Resolution

CLI resolution returns network coordinates, declared identity, and redacted
connection templates. It never emits a password or resolved secret.

Example:

```json
{
  "edge": {
    "id": "app-to-postgres",
    "type": "database",
    "from": {"service": "app"},
    "to": {"host": "db", "service": "postgresql", "port": 5432},
    "protocol": "postgresql",
    "secret_ref_count": 1,
    "secret_refs": ["db_password"],
    "secret_refs_truncated": false
  },
  "endpoint": {
    "host": "db.internal",
    "port": 5432,
    "protocol": "postgresql"
  },
  "connection_template": "postgresql://app:${secret:db_password}@db.internal:5432/app",
  "secret_refs": {
    "items": ["db_password"],
    "page": {
      "limit": 100,
      "returned": 1,
      "total": 1,
      "next_cursor": null
    }
  }
}
```

Credential-bearing CLI flags and output cannot be retained in `v0.2.0`. Before
release, consumer verification identifies every known use. Any consumer of the
unsafe behavior must migrate to the opaque in-process API or safe template
output before the release can proceed. The previous pinned revision remains
available during migration, so adoption is controlled rather than automatic.
No insecure serialization compatibility mode or escape hatch is provided.

## Secret Resolver Contract

The domain represents secrets by stable references. A provider adapter resolves
a reference to an opaque `SecretValue`.

`SecretValue`:

- has redacted string and representation forms
- rejects ordinary JSON serialization
- cannot be interpolated accidentally
- exposes plaintext only through an explicit trusted operation
- is retained only for the lifetime of the operation

Trusted in-process render consumers may explicitly reveal a value. The CLI and
future MCP interfaces cannot.

## Hosted Bitwarden Secrets Manager Adapter

The optional `infralink[bws]` extra uses Bitwarden's supported Python SDK
directly. It does not shell out to `bws`.

The adapter:

- accepts an explicit token or the fleet-standard `BWS_ACCESS_TOKEN`
- defaults to hosted Bitwarden endpoints
- supports only Bitwarden's hosted HTTPS endpoints in `v0.2.0`
- permits loopback endpoint configuration only through an injected fake SDK
  factory using a literal fake credential
- performs read-only operations
- supports project-scoped lookup
- returns normalized provider metadata and opaque values
- performs no disk caching

Endpoint configuration cannot be loaded from untrusted topology fields. The
Bitwarden Python SDK 2.1 does not expose redirect-policy, transport, or
response-origin hooks, so custom endpoints cannot meet the required
cross-origin redirect guarantee and are prohibited in `v0.2.0`. Custom endpoint
support remains deferred until a controllable SDK transport can reject a
different origin before authorization headers are sent. Tests cover override
rejection, fake-only loopback configuration, and token redaction.

Machine accounts must have read-only access to the minimum required projects.
Access tokens, provider payloads, and secret values are excluded from
representations, exceptions, logs, command capture, and artifacts.

`infralink secrets inspect` lists declared references and source locations
without contacting a provider.

`infralink secrets audit --provider bws` checks only references declared by the
loaded topology. It may report:

- reference identity
- source location
- project identity
- present or `unavailable_or_missing` state
- accessible or unavailable project state
- redacted provider error code
- check timestamp

It cannot perform arbitrary secret lookup or return a value.

If the optional dependency is absent, the CLI returns `provider_unavailable`
and an install action for `infralink[bws]`.

Provider-wide authentication or authorization failure means the adapter cannot
establish a session or access any configured project. The command then returns
`ok: false`, `provider_authentication_failed` or
`provider_authorization_failed`, and exit code `4`; it returns no partial audit
result.

After a session is established and at least one configured project is
accessible, another configured project absent from project-list metadata is a
project-level audit result. Every declared reference in that project reports
`accessible: false` with `project_unavailable`; the command completes with
`ok: true` and exit code `1`. The SDK listing surface cannot distinguish a
missing project from object-level authorization denial, so the public result
does not claim that distinction.

A declared reference absent from an accessible project's identifier metadata
reports `accessible: false` with `unavailable_or_missing`; the command
completes with `ok: true` and exit code `1`. Audit never calls `get()`, so it
does not invent a distinction between a nonexistent secret and per-object
authorization denial. Ambiguous provider-wide failures still fail closed
rather than returning a potentially misleading partial result.

Provider authentication, provider-wide authorization, timeout, and provider
availability failures remain distinguishable without exposing provider
response bodies. Missing-secret and per-object authorization outcomes are
intentionally combined as `unavailable_or_missing`.

Live BWS tests are opt-in and cannot run for untrusted pull requests. Default
tests use a fake resolver.

## Representative CLI Workflows

### Discover Commands

```bash
infralink
```

Returns the versioned command tree and safe entry-point actions.

### Validate Topology

```bash
infralink --registry registry.yml --edges edges.yml validate
```

Returns a structured validation result. Invalid topology is a completed negative
domain result with exit code `1`.

### Resolve An Edge

```bash
infralink resolve app-to-postgres
```

Returns the target identity, endpoint, safe connection template, required
secret references, and actions for validation or health checking.

### Audit Declared BWS References

```bash
infralink secrets audit --provider bws
```

Returns metadata-only availability results for declared references. It does not
accept an arbitrary secret identifier.

## Compatibility Policy

Before changing implementation behavior, tests characterize:

- public Python imports and call signatures
- package entry points
- CLI command names and options
- JSON envelope fields
- exit-code behavior
- reference-consumer usage

Compatible behavior is preserved. A change required by the approved secret
boundary must include:

- consumer search and compatibility evidence
- a migration action in CLI output where applicable
- release-note documentation
- a test preventing silent regression

The `v0.2.0` package is an opt-in adoption. Existing pinned revisions are not
updated automatically. The compatibility inventory records every known CLI
invocation and public Python call used by reference consumers. Woodpecker runs
those recorded workflows against the exact candidate artifact, not merely a
generic registry validation. If a safe result shape or exit code must change,
the affected consumer is migrated and verified before tagging.

Where safe, `infralink.cli/v1` is additive to the existing JSON envelope:
existing result fields retain their meaning while `schema_version`,
`command.parsed`, `command.resolved`, `meta`, and structured action fields are
added. There is no legacy mode for credential serialization.

The CLI envelope has its own version so future package releases can add fields
without silently changing established semantics. Removing or redefining
envelope fields requires a new envelope version.

## Verification

Release verification runs in this order.

### Core Verification

- pytest on all declared Python 3.10 through 3.12 versions
- Ruff lint and format checks
- strict mypy
- deterministic CLI contract tests

### Security Verification

- complete branch coverage of `SecretValue`
- complete branch coverage of CLI redaction
- complete branch coverage of the BWS adapter
- malicious and unexpectedly shaped provider payload tests
- repository secret scanning
- canary values that must not appear in logs or artifacts

Overall project coverage must reach at least 70 percent for `v0.2.0` and cannot
regress in later releases.

### Packaging Verification

- build wheel and source distribution
- inspect package metadata
- verify project URLs and version metadata
- install the wheel into a clean environment
- smoke-test module and console entry points
- verify the installed CLI reports `0.2.0`

### Public Integration

GitHub Actions tests the built artifact against sanitized example registries.
Public fixtures contain no internal hostnames, addresses, project identifiers,
or secret names.

### Private Compatibility

Woodpecker installs the exact built artifact and validates the authoritative
private registry checkout. This stage performs no deployment and publishes no
private inputs as artifacts.

The compatibility result gates release. Passing public tests without passing
the private consumer is insufficient.

### Artifact Provenance

GitHub Actions builds the release candidate once from a clean source commit and
produces:

- wheel and source-distribution bytes
- SHA-256 digest for each artifact
- source commit SHA
- package version
- workflow run identity

These values form a signed or CI-attested artifact manifest. Woodpecker obtains
the candidate bundle by immutable workflow artifact identity and verifies every
digest before testing. Its compatibility result records the same source commit
and artifact digests.

After the private gate passes, the release tag points to that source commit and
the GitHub release attaches the already-tested bytes. The release job verifies
the manifest again and does not rebuild the package.

## Release Process

After all gates pass:

1. Verify that the candidate source commit and artifact manifest passed the
   private Woodpecker gate.
2. Create annotated tag `v0.2.0` at that exact source commit.
3. Attach the already-tested wheel and source artifacts plus checksums to the
   GitHub release without rebuilding.
4. Publish migration notes and the CLI envelope version.
5. Update the infrastructure-management submodule in a separate reviewed
   change.
6. Retain the previous pinned Infralink revision as the immediate rollback
   target.

PyPI publication is deferred until the tagged artifact has been adopted and
operated successfully by the reference consumer.

## Explicit Exclusions

The following are not part of `v0.2.0`:

- MCP server implementation
- GitHub, Gitea, Woodpecker, Gatus, Prometheus, or Grafana API adapters
- fleet deployment or host changes
- service restart, shutdown, relocation, or consolidation
- registry mutation
- automated remediation
- PyPI publication
- broad internal rewrites
- log retrieval

These exclusions keep the first production-readiness slice focused on stable
contracts, secret safety, package quality, and controlled adoption.

## Acceptance Criteria

The design is implemented when:

- the existing failing suite is repaired without unrelated refactoring
- the documented Python compatibility surface is passing
- every CLI invocation follows `infralink.cli/v1`
- every command satisfies its documented minimum result contract
- root, help, and version discovery are JSON-only
- output and pagination policies are enforced
- credential values cannot cross the CLI serialization boundary
- recorded consumer workflows pass without an unsafe compatibility mode
- the vendor-neutral secret resolver and opaque value type are covered
- `infralink[bws]` performs metadata-safe, read-only hosted BWS resolution
- public CI, security, and packaging gates pass
- the exact artifact and its verified provenance manifest pass the private
  Woodpecker compatibility gate
- `v0.2.0` is tagged only after all required evidence is available
