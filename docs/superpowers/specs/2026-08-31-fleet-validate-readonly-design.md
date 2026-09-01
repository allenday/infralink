# Read-Only Fleet Validation Design

## Goal

Provide `infralink fleet validate` as the single public, read-only operation
for checking declared fleet state and bounded live evidence. It replaces the
validation portions of the legacy `infra-management/scripts` tools without
giving operators or agents a path to reconcile hosts.

## Decision

Validation never repairs state. A failed validation returns a completed,
structured negative result with a stable diagnostic and repair-oriented next
action. Reconciliation remains a private controller responsibility.

The command must not write files, reload services, run Docker or Ansible,
execute SSH, fetch BWS secrets, probe database root accounts, pull images, or
invoke controller reconciliation.

## Scope

The first slice migrates declaration-only validation from the legacy
`validate_roles.py` script:

- active-host role declarations resolve against declared role definitions;
- declared services resolve against rendered service declarations;
- required role parameters and dependencies are present;
- database-edge role and secret-reference conventions are valid; and
- declared compose and topology relationships emit stable diagnostics.

It defines an extension point for two later read-only evidence providers:

- Prometheus freshness checks currently represented by
  `check_prom_freshness.py`; and
- Prometheus QA checks currently represented by `prometheus_qa.py`.

`fleet_health.py` is explicitly out of scope because it combines validation
with artifact generation, Prometheus reload, BWS reads, remote shell, and
database-root checks.

## Command Contract

The public command tree is:

```text
infralink fleet validate [--live] [--strict] [--host HOST] [--limit N] [--cursor CURSOR]
```

`infralink fleet validate` is hermetic declaration-only validation. `--live`
enables only registered evidence providers whose contract is bounded and
read-only. The command uses the existing typed YAML or JSON envelope:

- transport and input errors use the central CLI failure envelope;
- invalid fleet state is a completed result with `ok: true` and
  `result.valid: false`;
- diagnostics are paginated with the existing cursor semantics; and
- `next_actions` link to inspection or controller-owned reconciliation
  documentation, never a direct mutation command.

The MCP projection exposes the same operation and result schema. CLI and MCP
may not diverge in available checks or mutation behavior.

## Architecture

The command is a new `fleet` Click group with a `validate` subcommand. Its
domain implementation is isolated from Click and receives immutable Registry
inputs plus an optional evidence-provider registry.

The declaration validator returns typed diagnostics only. It does not retain
legacy shell parsing or reach into the `infra-management` repository. The
first migration copies the tested semantic rules into Infralink-native typed
models and fixtures, then retires the legacy implementation only after parity
tests demonstrate equivalent failures for representative invalid declarations.

The live-evidence interface is deliberately narrow: a provider receives a
selected immutable host set and bounded configuration, then returns typed
diagnostics and evidence references. Providers cannot receive BWS credentials,
controller handles, or writable paths.

## Failure And Recovery

Missing declarations, stale evidence, unsupported live providers, and invalid
provider output fail closed as diagnostics. A transient evidence retrieval
failure is reported separately from a declaration failure so operators can
distinguish an unhealthy fleet from unavailable observation.

Every failed result includes at least one bounded next action. Initial actions
are limited to inspecting the relevant host, service, edge, or release
evidence. The controller reconciliation action is descriptive only until the
private controller API has a safe, separate authorization path.

## Testing

Tests must prove:

- declaration-only mode makes no network, subprocess, filesystem-write, or
  secret-provider call;
- valid and invalid Registry fixtures produce stable typed diagnostics;
- strict mode treats warnings as a completed negative result;
- YAML and JSON envelopes validate against generated schema and paginate
  diagnostics consistently;
- live providers are bounded, read-only, and cannot receive mutation
  dependencies; and
- CLI and MCP projections expose the same command and result semantics.

## Migration Boundaries

The command is not a compatibility wrapper around the legacy scripts. Each
legacy behavior moves only after it has a typed, tested ownership boundary:

| Legacy behavior | Destination |
| --- | --- |
| Role and compose declaration checks | `infralink fleet validate` static provider |
| Prometheus freshness and QA reads | later `infralink fleet validate --live` providers |
| Prometheus rendering and reload | private controller reconciliation |
| BWS scope and secret naming audits | private CI or controller validation |
| Database root connectivity probes | separately authorized verification operation |
| Ansible audits and remote shell | private controller or host-bootstrap workflows |

The legacy scripts remain deployed until their replacement has parity tests,
the controller has adopted any mutation path, and the corresponding
`infra-management` disposition issue records acceptance evidence.
