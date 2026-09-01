# Read-Only Fleet Validation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `infralink fleet validate` as the one public, read-only validation operation for declared fleet topology. It replaces the useful static semantics of legacy `infra-management/scripts/validate_roles.py` without copying its environment-secret checks, Jinja parsing, or mutable operational assumptions.

**Architecture:** The operation reads one explicit Infra Registry checkout and its companion declarations, builds an immutable validation report, and projects that report through the existing Click CLI, native MCP tool discovery, and typed agent-surface adapters. The validator is a pure domain function: it accepts loaded declarations and optional bounded evidence only, performs no subprocess, network, secret-manager, database, Docker, SSH, or controller calls, and returns structured diagnostics. The Click command only resolves existing explicit sources and renders the standard `infralink.cli/v1` envelope. A failed validation is a completed observation with a safe, repair-oriented next action; it never repairs, renders, reloads, or reconciles.

**Tech Stack:** Python 3.12, Pydantic v2, Click, `agent-surface`, MCP, PyYAML, pytest, Ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-08-31-fleet-validate-readonly-design.md`

## Global Constraints

- This is the migration target for `relax-dot-gg/infra-management#749`, under `cyberstorm-dev/infralink#304` and `#296`; do not delete or change the legacy script in this repository.
- Public command contract: `infralink fleet validate [--host HOST] [--strict] [--live] [--limit N] [--cursor CURSOR]`, with global `--registry`, `--edges`, and `--output` options. `--live` is accepted only when a registered bounded read-only evidence provider exists; this initial implementation must return an explicit capability-gap diagnostic rather than silently probing or falling back to host operations.
- `--host` filters by canonical host name, not UUID. An unknown host is a normal structured input error with a repair action, not an empty success result.
- Every result uses the established `infralink.cli/v1` envelope and pagination conventions. Diagnostics must be stable, structured, sorted, and bounded. Do not print secret names, secret values, environment values, file contents, or raw exception messages.
- Do not import `subprocess`, `os.environ`, Docker, BWS, SSH, database clients, controller adapters, or Ansible/Jinja renderers in the validator. Do not add `--check-secrets`, `--enforce-services`, `--apply`, reload, HUP, or reconcile behavior.
- The authoritative role/service catalog is `ansible/services.yml` inside the explicit registry checkout. The public request continues to identify only the checkout and optional edge file; it must not accept arbitrary ancillary paths that undermine one-source authority.
- The validator must use the registry schema and `EdgeSet` APIs where possible. It may parse the catalog as a small strict Pydantic model because current `Registry` only loads `hosts/`; parsing must reject malformed catalog shape deterministically.
- Legacy compose-template inspection is intentionally out of scope. A role’s declared `compose_service` is validated against the host’s declared service inventory, not rendered Jinja. Template/render conformance remains a private controller/registry concern until a rendered-artifact read model exists.
- Safe next actions may point to `infralink registry host get <host>` or `infralink edge show <edge>`. They must never propose an executable repair, controller reconcile, secret operation, or host apply.

## Validation Rules and Contract

The initial static provider must implement the following legacy-equivalent rules that are meaningful from declarative registry data:

| Rule | Severity | Diagnostic code | Evidence / repair |
| --- | --- | --- | --- |
| Active canonical names are unique | error | `duplicate_canonical_name` | Identify both host UUIDs; inspect each host declaration. |
| Active Tailnet names are unique when declared | error | `duplicate_tailscale_name` | Identify both host UUIDs; inspect each host declaration. |
| Each active host role exists in `ansible/services.yml:roles` | error | `unknown_role` | Inspect the host role or add a role declaration in Registry. |
| A role has all `requires_params` entries in its host `role_overrides[role]` | error | `role_parameter_missing` | Inspect the host declaration. |
| A role has every `requires_roles` dependency on the same host | error | `role_dependency_missing` | Inspect the host declaration. |
| A role with `compose_service` has that service in the host declared inventory | error | `role_compose_service_missing` | Inspect the host declaration; do not inspect templates. |
| Declared managed/legacy services known by `services` map to their `compose_service` in the host inventory | warning; error in `--strict` | `service_compose_service_missing` | Inspect the host declaration. |
| A catalog service is not declared by the host | warning | `unknown_service` | Inspect the host declaration or add the catalog entry. |
| A database edge has a valid auth role, role-specific username, database scope, and secret reference naming convention | error | `database_edge_auth_invalid` | Inspect that edge. |
| The catalog or required edges source is absent/malformed | operation error | existing `source_not_found` / `source_invalid` | Correct the explicit Registry checkout. |
| `--live` requested before a provider is registered | completed failed validation | `live_evidence_unavailable` | Use static validation now; add a bounded evidence provider in a later issue. |

Service inventory is the union of `managed_services`, legacy `services`, `unmanaged_services`, and template-derived services returned by `Host.services`. The diagnostic identity is `(code, subject_kind, subject_id, path)` and diagnostics are sorted by that tuple before pagination. No raw catalog or declaration text appears in results.

The result model must expose:

```python
class FleetValidationResult(BaseModel):
    valid: bool
    mode: Literal["static", "live"]
    diagnostics: Page[FleetValidationDiagnostic]
    summary: FleetValidationSummary

class FleetValidationSummary(BaseModel):
    host_count: int
    error_count: int
    warning_count: int
    capability_gap_count: int
```

`FleetValidationDiagnostic` must contain `code`, `severity` (`error`, `warning`, or `capability_gap`), `message`, `subject_kind`, `subject_id`, and optional `path`. Each message is a stable human sentence and each subject is an opaque registry UUID, canonical host name, or edge ID. `valid` is false for errors and capability gaps, and also false for warnings under `--strict`.

## File Structure

| File | Responsibility |
| --- | --- |
| `src/infralink/fleet/validation.py` | Pure catalog loader, static validation rules, deterministic diagnostic sorting, and result models. |
| `src/infralink/fleet/__init__.py` | Publicly exports only the typed request/result and validation entrypoint needed by adapters. |
| `src/infralink/operator_surface.py` | Defines `FleetValidateRequest` and registers `fleet.validate` as `read_only=True`; delegates to the domain function. |
| `src/infralink/cli/contracts.py` | Holds durable public envelope result models if the repository convention requires CLI contracts there instead of the domain module. Do not duplicate models. |
| `src/infralink/cli/fleet.py` | Thin Click `fleet` group and `validate` command. It translates `Context` paths to `FleetValidateRequest`, calls the typed operation, paginates the report, and emits the normal envelope. |
| `src/infralink/cli/main.py` | Registers the lazy `fleet` group, command metadata, help metadata, and root safe navigation action. |
| `tests/test_fleet_validation.py` | Pure static-rule and deterministic-result coverage with temporary registry fixtures. |
| `tests/test_cli_fleet_validation.py` | Click envelope, exit-code, filtering, pagination, strict, live capability-gap, and next-action coverage. |
| `tests/test_agent_surface_projection.py` | Adds `fleet.validate` direct Click/MCP adapter parity and confirms it is read-only. |
| `tests/test_mcp_server.py` | Confirms the native `infralink_fleet_validate` MCP tool is discoverable and accepts only the documented parameters. |
| `docs/commands/fleet-validate.md` | Operator-facing BLUF command reference with explicit safety and scope boundaries. |
| `README.md` or existing command index | Links to the command reference without duplicating the full contract. |

## Implementation Tasks

### Task 1: Establish failing contract tests and fixtures

**Files:**
- Create `tests/test_fleet_validation.py`
- Create `tests/test_cli_fleet_validation.py`
- Modify `tests/test_agent_surface_projection.py`
- Modify `tests/test_mcp_server.py`

- [ ] Add a fixture builder that writes a minimal explicit registry checkout: `hosts/<uuid>/manifest.yml`, `network/main-dev/edges/edges.yml`, and `ansible/services.yml`. Use temporary paths and documentation UUIDs only.
- [ ] Write pure-domain tests before implementation for every table rule above: unknown role, missing role parameter, missing role dependency, missing role compose service, catalog service mismatch, unknown service, duplicate active canonical/Tailnet names, database auth mismatch, stable sort, `--host` filtering, and malformed/missing catalog.
- [ ] Test strict behavior separately from collection: warnings remain in the result in both modes, while only strict changes `valid` and process exit status.
- [ ] Add CLI tests asserting `infralink --registry <root> --edges <path> fleet validate --output json` returns the standard envelope, includes `command.parsed.path == ["fleet", "validate"]`, and exits 0/1 according to `result.valid`.
- [ ] Add tests that `--live` returns an ordinary completed result with `live_evidence_unavailable`, no exception, `mode == "live"`, `valid is False`, and only safe inspect actions.
- [ ] Add direct `ClickAdapter(operator_surface, ...)` and `MCPAdapter(operator_surface, ...)` parity tests for `fleet.validate`; assert equal result payloads and that any returned actions are `safe: true`.
- [ ] Add native MCP discovery/invocation coverage for `infralink_fleet_validate`, ensuring no input schema contains write, apply, secret, credential, or arbitrary source-path options.

### Task 2: Add pure fleet validation models and catalog loading

**Files:**
- Create `src/infralink/fleet/__init__.py`
- Create `src/infralink/fleet/validation.py`

- [ ] Define frozen Pydantic models for catalog roles/services. Model only keys used by the validation contract: `requires_params`, `requires_roles`, `compose_service`, and catalog service `compose_service`. Permit irrelevant catalog metadata only if it remains unobserved; reject a missing/non-mapping `roles` or `services` section with an `OperationError("source_invalid", ...)` at the catalog path.
- [ ] Add `load_role_service_catalog(registry_root: Path) -> RoleServiceCatalog`, requiring exactly `<registry>/ansible/services.yml`. Use `yaml.safe_load`; convert parser/schema failures to a bounded `OperationError` with `source: "role_service_catalog"`, the resolved path, and a repair-oriented fix.
- [ ] Define `FleetValidationDiagnostic`, `FleetValidationSummary`, and `FleetValidationResult` once. Reuse the repository’s public `Page` type rather than defining pagination locally.
- [ ] Implement `validate_fleet(loaded: LoadedSources, *, host: str | None, strict: bool, live: bool) -> FleetValidationResult`. Keep it pure after source loading and make every rule a small private function that appends typed diagnostics.
- [ ] Filter only active hosts. Resolve `--host` by canonical name before validation; if no active matching host exists, raise `OperationError("host_not_found", ...)` with no host enumeration.
- [ ] Express database-edge auth checks from legacy constants in this module, preserving conventions for `admin`, `ops`, `rw`, and `ro`, PostgreSQL/MariaDB protocol aliases, scoped database usernames, and expected secret-ref names. Emit one deterministic `database_edge_auth_invalid` diagnostic per invalid field rather than leaking unredacted edge auth data.
- [ ] For `live=True`, append exactly one `capability_gap` diagnostic and do not instantiate any provider, perform any I/O beyond the declared static sources, or change the static diagnostic set. The future provider extension point is an internal protocol, not a public plugin path in this slice.
- [ ] Sort diagnostics by the specified identity and calculate the summary and `valid` only after collecting all diagnostics.

### Task 3: Register the read-only typed operation

**Files:**
- Modify `src/infralink/operator_surface.py`
- Modify `src/infralink/agent_surface.py` only if the renderer needs a source field added; otherwise leave it unchanged.

- [ ] Add `FleetValidateRequest(SourceRequest)` with `host: str | None`, `strict: bool = False`, and `live: bool = False`. Do not include `limit`, `cursor`, or `collection`: they are transport pagination controls, not domain input.
- [ ] Register `@operator_surface.operation("fleet.validate", summary="Validate declared fleet topology", read_only=True)` and delegate to `load_sources()` plus `validate_fleet()`.
- [ ] Ensure `operator_surface.operations.describe("fleet.validate").read_only is True`; this is the authority for projected safe actions.
- [ ] Keep the canonical agent-surface operation free from a Click `Context`, environment selectors, controller config, and ambient source fallback.
- [ ] Do not widen `InfralinkEnvelopeRenderer` source inheritance unless a test demonstrates it is needed. The request already uses the standard `registry` and `edges` fields.

### Task 4: Add the public Click and native MCP command

**Files:**
- Create `src/infralink/cli/fleet.py`
- Modify `src/infralink/cli/main.py`
- Modify `src/infralink/mcp_server.py` only if native discovery cannot recurse into the new group without a targeted adjustment.

- [ ] Create a `fleet` Click group and a `validate` subcommand with `--host`, `--strict`, `--live`, and standard `_page_options`. Keep all source resolution in the existing root context.
- [ ] Build `FleetValidateRequest(registry=ctx.registry_path, edges=ctx.edges_path, ...)` only after the normal root configuration checks; invoke the typed `fleet_validate` operation, not a copied implementation.
- [ ] Paginate the already sorted diagnostic list using `_active_collection`, `_page_offset`, `page_items`, and `_attach_next_cursors`. Use a fingerprint derived from selected source revisions/paths, host filter, strict/live flags, and complete diagnostic identity set, as existing validate/check commands do.
- [ ] Emit via `_emit_query_result` with path `["fleet", "validate"]` and exit 0 iff `result.valid`; do not cause an error envelope for a validation failure.
- [ ] When diagnostics exist, construct only safe HATEOAS actions: `registry host get <host>` for host diagnostics and `edge show <edge>` for edge diagnostics. Deduplicate by argv and preserve diagnostic order. Add a safe static retry action for a live capability gap rather than a controller-repair action.
- [ ] Add `fleet` to `COMMAND_METADATA`, root help metadata, `_load_command`, and the root navigation. Its help must state that it observes declarations only and does not reconcile hosts.
- [ ] Confirm native MCP path discovery produces `infralink_fleet_validate` and projects the same Click envelope. Do not introduce a second custom MCP transport.

### Task 5: Document the operational boundary

**Files:**
- Create `docs/commands/fleet-validate.md`
- Modify the canonical command index in `README.md` or `docs/README.md`
- Modify generated CLI schema only if normal schema generation changes it

- [ ] Start the command reference with BLUF: this is the safe declaration-validation command; a failure identifies invalid desired state and the next safe inspection, while controller reconcile remains private and separate.
- [ ] Include the exact command synopsis, input authority (`--registry`, optional `--edges`), output/exit semantics, all three diagnostic severities, and a compact rule table linking each family to its repair boundary.
- [ ] Document the explicit non-goals: no secret presence checking, no BWS, no DB root access, no Jinja rendering, no Docker/SSH/host reachability, and no repair/reconcile/reload.
- [ ] Explain `--live` as a capability-gap placeholder in this slice, not a misleading health probe. Link to the issue that will add bounded Prometheus/QA evidence.
- [ ] Update command navigation with links only; do not duplicate the full reference in the root README.

### Task 6: Verify public contracts and generated artifacts

**Files:**
- Modify generated schema files only if the repository generators report deterministic changes.

- [ ] Run focused tests first:

```bash
.venv/bin/python -m pytest tests/test_fleet_validation.py tests/test_cli_fleet_validation.py tests/test_agent_surface_projection.py tests/test_mcp_server.py
```

- [ ] Run formatting, lint, types, docs, and schema generation:

```bash
.venv/bin/python -m ruff format --check src tests scripts
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m mypy src scripts
.venv/bin/python scripts/check_docs.py
.venv/bin/python scripts/generate_cli_schemas.py
git diff --exit-code
```

- [ ] Run the full test suite in the project virtual environment:

```bash
.venv/bin/python -m pytest
```

- [ ] Run a final static smoke fixture through both transports. The CLI invocation must not access the network or mutate files; capture only sanitized envelope output in test assertions.

```bash
.venv/bin/infralink --registry /tmp/fixture-registry --edges /tmp/fixture-registry/network/main-dev/edges/edges.yml --output json fleet validate
.venv/bin/infralink mcp serve
```

- [ ] Before opening the PR, inspect `git diff --check`, `git status --short`, command help, and generated schema diffs. Create a conventional draft PR referencing `infralink#304`, `#296`, and `infra-management#749`; do not merge or push to `main`.

## Deferred Follow-Up Slices

- [ ] Add a registered bounded `--live` evidence provider that consumes a declared, read-only Prometheus/QA snapshot. It must be separately designed and tracked before `--live` becomes a successful observation mode.
- [ ] Move rendered Compose conformance into a signed controller-produced artifact read model; only then add a read-only renderer/artifact comparison to fleet validation. Do not reintroduce Jinja include parsing in the public client.
- [ ] Retire `infra-management/scripts/validate_roles.py` only after the command has been accepted against real Registry declarations and its remaining secret/render-only checks have owned private replacements or explicit retirement decisions.
