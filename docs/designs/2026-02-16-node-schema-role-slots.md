# NodeSchema + Role Slots Design

## Summary
Introduce an abstract `NodeSchema` superclass to unify shared identity/metadata across nodes, and extend role templates (`RoleConfig`) with typed, required-by-default dependency slots. This enables stricter edge validation (node-type compatibility) and validates that role instances bind required dependencies.

## Goals
- Make node types explicit (`host`, `service`) to validate edges against sensible node-type pairs.
- Keep registry layout stable (host-keyed manifests remain unchanged).
- Add typed role slots to declare dependencies cleanly and enforce bindings.
- Preserve backward compatibility for existing manifests.

## Non-Goals
- Restructuring registry to a single `nodes` list.
- Auto-generating edges from slots (future enhancement).
- Adding resource nodes (planned but not in this change).

## Architecture

### NodeSchema (abstract)
A shared base for node identity and metadata. It adds a `node_type` discriminator and common fields so that both `HostSchema` and `ServiceSchema` can inherit from it.

**Fields (proposed):**
- `node_type: Literal["host", "service"]` (defaulted in subclasses)
- `canonical_name: str`
- `status: HostStatus` (or shared Status enum)
- `group: str | None`
- `notes: str | None`
- `tags: list[str]` (optional)
- `metadata: dict[str, Any]` (provider-agnostic)
- `created: str | None`, `updated: str | None`

`HostSchema(NodeSchema)` retains network, mounts, BWS config, observability, etc. `ServiceSchema(NodeSchema)` retains service catalog fields and role definitions. For backward compatibility, `HostSchema.node_type` defaults to `"host"` when absent.

### Role Slots (typed dependencies)
Extend `RoleConfig` with `slots: dict[str, SlotConfig]`, where each slot describes a typed dependency.

**SlotConfig fields:**
- `type: Literal["database", "queue", "smtp", "storage", "api", ...]`
- `required: bool = True`
- `protocol: str | None`
- `role: str | None` (e.g. rw/ro/admin)
- `notes: str | None`

#### Bindings
Role overrides on a host can provide `slot_bindings`:
```yaml
roles:
  - wordpress
role_overrides:
  wordpress:
    slot_bindings:
      db:
        host: database.example.com
        service: mariadb
        role: rw
      smtp:
        host: mail.example.com
        service: postfix
        protocol: smtp
```

Slots are **required by default**; a missing required binding fails validation.

## Validation

### Node-type edge validation
Add a validation check in `EdgeSchema`/registry validation to enforce allowed node-type pairs:
- `service → service` (default)
- `host → service` (monitoring/infra probes)
- `host → host` only for explicit security/mesh edge types (optional)

### Slot binding validation
For each host:
1) For every role in `roles`, load its `RoleConfig`.
2) If role has `slots`, require bindings for required slots.
3) Each binding must resolve to an existing host and a declared service on that host.

This complements edge validation and prevents missing dependencies even before edges are rendered.

## Data Flow Impact
Registry loading is unchanged. The validation step becomes stricter:
- `NodeSchema` ensures consistent typing.
- `RoleConfig.slots` ensures dependency binding completeness.
- Edge validation enforces node-type compatibility.

## Backward Compatibility
- If `node_type` is missing on hosts, default it to `"host"`.
- Role slots are additive; existing roles without slots are unchanged.
- Edges remain host/service-based; slot bindings are an extra contract.

## Implementation Outline
1) Add `NodeSchema` to `core/schema.py` and move shared fields from `HostSchema`.
2) Make `HostSchema(NodeSchema)` and `ServiceSchema(NodeSchema)` with defaults for `node_type`.
3) Add `SlotConfig` and extend `RoleConfig` with `slots`.
4) Add validation for role slot bindings in registry validation flow.
5) Add edge validation for node-type compatibility.
6) Update/extend tests for defaults, slot binding requirements, and invalid edges.

## Open Questions
- Should we allow `host → host` edges for only certain edge types (e.g., `security`)?
- Should slot bindings be allowed to reference wildcard services (`"*"`) for host-level dependencies?
