# Infralink Product Backlog

## v0.2 Foundation

- [x] Typed registry, services, roles, and edges
- [x] Resolver endpoints and safe connection templates
- [x] TCP, HTTP, and Redis health checks
- [x] JSON-only `infralink.cli/v1` command contracts
- [x] Bounded topology and application queries
- [x] Deterministic diagram and documentation artifacts
- [x] Opaque secret values and declared-reference inventory
- [x] Optional read-only hosted BWS adapter and metadata audit
- [x] Strict typing, Ruff, branch coverage, schema, and package gates
- [x] Deterministic candidate manifest and public-data boundary policy
- [x] Manual SHA-bound, build-once candidate workflow

## Next

- [ ] Verify attested candidates in the private consumer CI
- [ ] Run canary validation against sanitized topology fixtures
- [ ] Tag `v0.2.0` only after private verification and explicit approval
- [ ] Migrate consumers from legacy URL helpers to connection templates
- [ ] Migrate consumers to `infralink.cli/v1` envelopes and cursors
- [ ] Add PostgreSQL and MySQL query checks
- [ ] Add retry and timeout policy controls
- [ ] Add registry diff and impact analysis
- [ ] Add Prometheus metrics and configuration generation
- [ ] Add Jinja2 integration for safe templates
- [ ] Evaluate custom BWS endpoints after the hosted-only boundary stabilizes

## Deferred

- Dynamic service discovery
- Deployment orchestration
- Secret writes or arbitrary secret lookup
- Automatic publication or deployment from candidate CI
- Non-POSIX support for transactional artifact commands

*Last updated: 2026-07-25*
