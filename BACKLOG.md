# Infralink Product Backlog

Current package version: `0.6.0`.

## Completed Foundation

- [x] Typed registry, services, roles, and edges.
- [x] Resolver endpoints and safe connection templates.
- [x] TCP, HTTP, and Redis health checks.
- [x] Stable CLI envelopes, bounded outputs, cursors, and exit codes.
- [x] Bounded topology and application queries.
- [x] Deterministic diagram and documentation artifacts.
- [x] Opaque secret values and declared-reference inventory.
- [x] Optional read-only hosted BWS adapter and metadata audit.
- [x] Strict typing, Ruff, branch coverage, schema, and package gates.
- [x] Deterministic public-data boundary and package policy.
- [x] Manual main-bound Woodpecker release contract.
- [x] Release candidate, publisher request, and attestation contracts through v3.
- [x] Offline observation contracts, diagnostics, readiness suites, and profile-scoped operations views.

## Current Next Work

- [ ] Keep contributor and agent onboarding docs current as command surfaces change.
- [ ] Add optional private compatibility diagnostics in Woodpecker without weakening public CI.
- [ ] Run canary validation against sanitized topology fixtures.
- [ ] Migrate consumers from legacy URL helpers to connection templates.
- [ ] Migrate consumers to structured CLI envelopes and cursors.
- [ ] Add PostgreSQL and MySQL query checks.
- [ ] Add retry and timeout policy controls.
- [ ] Add registry diff and impact analysis.
- [ ] Add Prometheus metrics and configuration generation.
- [ ] Add Jinja2 integration for safe templates.
- [ ] Evaluate custom BWS endpoints only after the hosted-only boundary has a design and tests.

## Deferred

- Dynamic service discovery.
- Deployment orchestration.
- Secret writes or arbitrary secret lookup.
- Automatic or non-main publication and production deployment.
- Non-POSIX support for transactional artifact commands.

## Historical Notes

The `v0.2` foundation and migration inventory remain useful history. See
[docs/compatibility/v0.2.md](docs/compatibility/v0.2.md) and
[docs/releases/](docs/releases/) for version-specific notes. Do not trigger old
release versions from backlog text; release publication requires explicit human
approval through the protected Woodpecker workflow.

*Last updated: 2026-08-18*
