# RelayOS Observable Adoption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use \`superpowers:subagent-driven-development\` (recommended) or \`superpowers:executing-plans\` to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Add a generic, versioned observable topology contract that represents host, service, component, endpoint, edge, and resource ownership; prove RelayOS staging can adopt it without changing rendered Compose or recreating a container.

**Architecture:** Keep \`infralink.observation/v1\` immutable and readable. Add \`infralink.observation/v2\`, in which a service profile owns named component slots and resource-slot requirements, a service instance binds those slots on a host, and component instances own endpoint/capability/metric contracts. The planner normalizes each schema version into stable host/service/component/endpoint/edge identities without reinterpreting v1 documents as v2. Doctor's generic static adoption-readiness gate consumes version-qualified compiled plans and reports evidence boundaries without making network or runtime changes. Infra-registry then adds RelayOS declarations as data-only observation contracts and compares generated projections with the current monitoring configuration before enabling a new observer.

**Tech Stack:** Python 3.10+, Pydantic v2, Click, JSON Schema Draft 2020-12, pytest, YAML, existing Infralink observation planner and Doctor CLI.

**Dependencies and ownership:**

- InfraLink issue [#173](https://github.com/cyberstorm-dev/infralink/issues/173) owns the generic \`infralink doctor host <host> --validate\` adoption-readiness command. Do not fork that behavior into a RelayOS-specific command.
- infra-management issue [#411](https://github.com/relax-dot-gg/infra-management/issues/411) coordinates the RelayOS rollout and accepts the registry projection/evidence review.
- This plan does not authorize an \`apply\`, a Compose render change, an image pull, a container recreation, or live probe activity. Those require a separately approved protected-service transition.

---

## Invariants

1. \`infralink.observation/v1\` keeps its current schema and semantics: a v1 \`ServiceProfile\` remains a component-level profile. No v1 document is silently reinterpreted as a service-with-components.
2. A v2 service is the aggregate; components are its concrete runtime units. A component has one stable instance identity even when two components reuse the same component profile.
3. A source-planning invocation accepts exactly one source schema version.
   Cross-version adoption is permitted only after each source set has compiled
   into an explicit plan with a qualified schema identity, such as
   \`infralink.plan.v1\` or \`infralink.plan.v2\`.
4. Incremental adoption may aggregate separately compiled v1 and v2 plans at
   generic read-only boundaries such as Doctor. Aggregation must preserve each
   schema's semantics and qualified identities, join plans only at documented
   aggregate boundaries such as host readiness evidence, and must not silently
   convert v1 component-profile meaning into v2 service/component meaning.
5. Components, not services, own endpoints, health capabilities, metric capabilities, log capabilities, and resource bindings. A service owns aggregate readiness only.
6. Resources are typed \`config\`, \`secret\`, or \`storage\` contracts. A resource reference exposes only identity, delivery form, and declared path/ownership metadata; no secret value enters plans, schemas, diagnostics, or generated artifacts.
7. An edge always targets a component endpoint. A required-edge failure affects its source service readiness; it does not make the target unhealthy by implication.
8. Node exporter and cAdvisor remain host-baseline capabilities. They are not invented as RelayOS application components.
9. Registry observation adoption is data-only until static plan parity, observer configuration, and live evidence are independently reviewed. Existing legacy checks remain the operational fallback until that review succeeds.

---

### Task 1: Freeze the v1/v2 compatibility boundary with failing model tests

**Files:**
- Create: \`tests/test_observation_v2_models.py\`
- Modify: \`src/infralink/observation/models.py\`
- Modify: \`src/infralink/observation/loader.py\`

**Step 1: Write failing tests**

Add tests that load one v1 fixture and one v2 fixture in separate calls, then assert:

\`\`\`python
assert v1_report.valid
assert v2_report.valid
assert v1_plan.schema_version == "infralink.plan.v1"
assert v2_plan.schema_version == "infralink.plan.v2"
\`\`\`

Add negative tests for a v2 service profile with duplicate component-slot IDs, a service instance missing a required slot, a component instance using an unknown slot, and a resource binding that embeds a secret-like value.

**Step 2: Run the focused test to verify it fails**

Run:

\`\`\`bash
.venv/bin/python -m pytest tests/test_observation_v2_models.py -q
\`\`\`

Expected: FAIL because v2 documents are currently rejected as an unsupported schema version.

**Step 3: Implement the additive v2 source models**

In \`models.py\`, add separate v2-only types instead of changing existing v1 classes:

- \`ComponentProfile\` with component-owned endpoint, health, metric, log, and signal declarations.
- \`ResourceKind\` enum (\`config\`, \`secret\`, \`storage\`) and \`ResourceSlot\` with required status, allowed delivery forms, declared mount/path metadata, and purpose.
- \`ServiceComponentSlot\` with stable slot ID, \`component_profile_id\`, required resource slot IDs, and optional display name.
- \`ServiceProfileV2\` with named component slots, service-level readiness policy, and required host-baseline capabilities.
- \`ComponentInstance\` with stable ID, slot ID, endpoint selection/overrides, and resource-binding IDs.
- \`ServiceInstanceV2\` with host ID, v2 profile ID, and component instances.
- \`ResourceBinding\` with a declared resource kind plus the existing alias/renderer identity pattern for secrets. Reuse the inline-secret detector for every metadata field.
- \`DependencyContractV2\` whose source service and target component endpoint are qualified references.

Enforce stable uniqueness at the narrowest correct scope: service component slots per profile, component IDs per service instance, endpoint IDs per component profile, and resource bindings per component instance.

In \`loader.py\`, recognize both explicit schema versions and keep separate
identity collection maps for v1 and v2. The planner must reject a source batch
that mixes v1 and v2 documents in one planning invocation, because there is no
implicit cross-version conversion step. That rejection does not prevent a
higher-level read-only consumer from invoking the planner separately for v1 and
v2 sources and aggregating the resulting plans under the explicit coexistence
contract above.

**Step 4: Run the focused test to verify it passes**

Run:

\`\`\`bash
.venv/bin/python -m pytest tests/test_observation_v2_models.py -q
\`\`\`

Expected: PASS; v1 remains accepted, valid v2 contracts load, and invalid v2 contracts fail with bounded diagnostics.

**Step 5: Commit**

\`\`\`bash
git add src/infralink/observation/models.py src/infralink/observation/loader.py tests/test_observation_v2_models.py
git commit -m "feat: add versioned observable topology contracts"
\`\`\`

---

### Task 2: Compile v2 service/component topology into stable plan identities

**Files:**
- Create: \`tests/test_observation_v2_planner.py\`
- Modify: \`src/infralink/observation/planner.py\`
- Modify: \`src/infralink/observation/canonical.py\` only if a shared qualified-reference helper is needed

**Step 1: Write failing planner tests**

Use a single-host fixture containing one service with two different components and one service with two instances of the same component profile. Assert exact identity ownership:

\`\`\`python
assert plan.services[0].id == "<host>/relay-os"
assert {component.id for component in plan.components} == {
    "<host>/relay-os/nginx",
    "<host>/relay-os/lego",
}
assert plan.endpoints[0].component_id == "<host>/relay-os/nginx"
assert plan.dependencies[0].target_component_id == "<host>/relay-os/nginx"
\`\`\`

Cover errors for unresolved component slot, duplicate component identity, resource kind mismatch, missing required resource binding, and an edge whose endpoint belongs to a different component than declared.

**Step 2: Run the focused test to verify it fails**

\`\`\`bash
.venv/bin/python -m pytest tests/test_observation_v2_planner.py -q
\`\`\`

Expected: FAIL because the current planner produces only v1 service/profile/endpoint identities.

**Step 3: Implement a distinct v2 planner path**

In \`planner.py\`:

- Add \`PlannedComponent\`, \`PlannedResourceRequirement\`, and \`PlannedResourceBinding\` models.
- Add component ownership fields to \`PlannedEndpoint\`, \`PlannedSignal\`, and \`PlannedDependency\`; preserve the existing v1 classes or define v2 counterparts if that avoids changing the v1 plan schema.
- Add \`PlanV2\` with \`schema_version: "infralink.plan.v2"\`; retain \`Plan\` as \`infralink.plan.v1\`.
- Resolve IDs deterministically as \`<host>/<service>/<component>\` and endpoint IDs as \`<host>/<service>/<component>/<endpoint>\`.
- Compile aggregate service readiness from required component signals plus source-owned required dependency signals.
- Keep host baseline capabilities as \`PlannedHost\` properties; do not emit exporter components.
- Preserve source references for profile, service instance, component instance, resource binding, and edge declarations so diagnostics identify the correct YAML field.

Do not add implicit migration or a v1-to-v2 converter. The compiler must select its path from the document schema version and emit a plan whose schema version and identity namespace remain explicit for downstream aggregation.

**Step 4: Run the focused test to verify it passes**

\`\`\`bash
.venv/bin/python -m pytest tests/test_observation_v2_planner.py tests/test_cli_observation.py -q
\`\`\`

Expected: PASS; v2 output is deterministic and existing v1 CLI projection tests remain green.

**Step 5: Commit**

\`\`\`bash
git add src/infralink/observation/planner.py src/infralink/observation/canonical.py tests/test_observation_v2_planner.py
git commit -m "feat: compile component-owned observation topology"
\`\`\`

---

### Task 3: Publish v2 schemas and offline projection output

**Files:**
- Modify: \`scripts/generate_observation_schemas.py\`
- Create: \`src/infralink/schemas/observation/v2/\`
- Modify: \`src/infralink/observation/api.py\`
- Modify: \`tests/test_cli_observation.py\`
- Modify: \`examples/observation/\` with sanitized v2 fixture documents

**Step 1: Write failing CLI and schema tests**

Add a v2 fixture with nginx and lego components. Assert that \`infralink observation validate\` and \`infralink observation project\` expose component IDs, component-owned endpoints, resource requirements, aggregate readiness, and edge ownership without secret values.

Validate generated v2 schemas with \`Draft202012Validator\` and assert v1 schema files remain byte-for-byte unchanged.

**Step 2: Run the focused test to verify it fails**

\`\`\`bash
.venv/bin/python -m pytest tests/test_cli_observation.py -q
\`\`\`

Expected: FAIL because the schema generator and public project result only know v1 document and plan shapes.

**Step 3: Implement v2 schema/projection support**

- Extend \`generate_observation_schemas.py\` to emit v2 document schemas under \`src/infralink/schemas/observation/v2/\` without altering v1 output.
- Make \`api.project()\` serialize the selected versioned plan, including components and typed resources in v2 output.
- Keep the CLI envelope version unchanged; versioned topology belongs under \`result\`, not in an ad hoc response envelope.
- Add sanitized public v2 fixtures for a host baseline, service profile, component profiles, component instances, resources, and a dependency edge.

**Step 4: Regenerate and verify**

\`\`\`bash
.venv/bin/python scripts/generate_observation_schemas.py
.venv/bin/python -m pytest tests/test_cli_observation.py -q
git diff --check
\`\`\`

Expected: PASS; only new v2 schema files and deliberate projection changes are present.

**Step 5: Commit**

\`\`\`bash
git add scripts/generate_observation_schemas.py src/infralink/observation/api.py src/infralink/schemas/observation/v2 examples/observation tests/test_cli_observation.py
git commit -m "feat: project versioned component observation contracts"
\`\`\`

---

### Task 4: Extend generic Doctor adoption readiness, coordinated with issue #173

**Files:**
- Modify: \`src/infralink/cli/doctor.py\` and the current Doctor planning/readiness module identified by the #173 implementation
- Modify: \`tests/test_cli_doctor.py\`
- Modify: \`tests/test_host_readiness.py\` only if host readiness types change

**Step 1: Write failing static-validation tests**

Add a v2 host fixture and assert \`infralink doctor host <host> --validate\` performs no network calls and returns structured findings for:

- unresolved host/canonical identity;
- missing v2 desired contract or an explicit preservation-only state;
- unresolved component/resource/endpoint/edge compilation;
- missing immutable-image or one-time evidence-bound adoption evidence;
- missing declared secret alias/delivery compatibility;
- observation contracts that compile but have no live evidence, reported as \`unknown\` rather than healthy;
- declared baseline exception distinguished from a migration regression.

The test must assert no SSH, Docker, Compose, image pull, reconcile request, or persisted-plan write occurs.

**Step 2: Run the focused test to verify it fails**

\`\`\`bash
.venv/bin/python -m pytest tests/test_cli_doctor.py tests/test_host_readiness.py -q
\`\`\`

Expected: FAIL until the generic #173 readiness implementation can consume a v2 compiled plan.

**Step 3: Implement only generic readiness behavior**

Coordinate the exact files with the owner of #173, then:

- feed independently compiled v1 and v2 plans through the existing \`doctor host --validate\` boundary when a host or fleet is adopted incrementally;
- preserve each input plan's schema semantics and qualified identities in Doctor findings, including v1 component-level service profiles and v2 service/component aggregates;
- aggregate readiness across plan versions only through explicit Doctor evidence boundaries, and forbid implicit semantic conversion of v1 plan objects into v2 plan objects;
- evaluate v2 topology at component/resource/endpoint/edge granularity;
- report static contract validity separately from live evidence status;
- preserve bounded, secret-free findings and the existing CLI envelope;
- expose a typed, machine-readable adoption result suitable for registry CI to consume later.

Do not encode RelayOS service names, 27-container counts, or legacy monitoring exceptions in InfraLink.

**Step 4: Run the focused test to verify it passes**

\`\`\`bash
.venv/bin/python -m pytest tests/test_cli_doctor.py tests/test_host_readiness.py -q
\`\`\`

Expected: PASS; static adoption readiness is generic, offline, fail-closed for declaration errors, and \`unknown\` for absent live evidence.

**Step 5: Commit**

\`\`\`bash
git add src/infralink/cli/doctor.py src/infralink tests/test_cli_doctor.py tests/test_host_readiness.py
git commit -m "feat: validate component topology adoption readiness"
\`\`\`

---

### Task 5: Add RelayOS staging's data-only v2 observation declarations in infra-registry

**Repository:** \`relax-dot-gg/infra-registry\` (separate PR; do not add host-local files)

**Files:**
- Create: the registry's canonical v2 observation documents for RelayOS staging, colocated with existing observation/catalog declarations
- Modify: the registry compiler tests and any canonical service catalog documents required for typed profile reuse
- Do not modify: rendered RelayOS Compose, image declarations, secrets, listener/firewall declarations, or the legacy verification checks in this task

**Step 1: Write failing registry compiler/projection tests**

Use RelayOS staging's actual host UUID and declare these service instances:

- \`relayos-edge\`: \`nginx\`, \`lego\`, \`lego-reloader\`;
- \`relayos-web\`: one \`wordpress\` component per current tenant instance;
- \`relayos-irc-stack\`: \`inspircd\`, \`anope\`, \`kiwiirc\`, \`kiwibnc\`, and \`webircgateway\` per tenant instance;
- \`chatflow\`: \`chatflow\` and \`watchdog\`;
- \`edge-prober\` on its actual listener port \`9119\`.

Assert generated plan identities, required dependency edges, resource references, and metric contracts exactly match the current intended runtime. Assert node exporter and cAdvisor remain host baseline. Assert no desired Compose artifact changes.

**Step 2: Run tests to verify they fail**

Run the registry's compiler/manifest tests and the generic Doctor command against the generated observation source.

Expected: FAIL because RelayOS has no v2 observation declarations yet.

**Step 3: Add only canonical declarations**

- Define reusable component profiles in the service catalog; do not define host-specific component types.
- Bind the RelayOS staging host to service instances and components using stable IDs derived from its existing manifest/service identity.
- Reference existing BWS aliases and declared host-mounted resources by logical identity only.
- Correct the edge-prober catalog declaration from \`9115\` to the verified listener \`9119\` in the typed observation contract. Retain the old operational check until projected parity is reviewed.
- Model required edges such that a dependency failure is attributed to its source service.

**Step 4: Verify data-only parity**

\`\`\`bash
infralink observation validate --source <registry-observation-source>
infralink observation project --source <registry-observation-source> --output json
infralink doctor host <relayos-staging> --validate --observation-plan <generated-plan>
git diff -- <rendered-relayos-compose-path>
\`\`\`

Expected: valid static topology, \`unknown\` where no configured live observer has evidence, and no Compose diff.

**Step 5: Commit and open a draft PR**

\`\`\`bash
git add <canonical-observation-files> <registry-tests>
git commit -m "feat: declare RelayOS component observation topology"
git push -u origin <branch>
\`\`\`

The draft PR must link #411 and include the generated plan digest, the no-Compose-diff evidence, and a table mapping each new typed contract to the legacy check it shadows.

---

### Task 6: Configure observers and prove projection parity before enablement

**Repository:** \`relax-dot-gg/infra-management\` and/or the canonical registry renderer repository, depending on the established controller ownership.

**Files:**
- Modify only the canonical projection/rendering source that generates Prometheus, Gatus, Loki labels, and Grafana references from typed observation contracts
- Modify/add focused projection tests

**Step 1: Write failing parity tests**

For the RelayOS v2 plan, assert:

- component metric contracts produce Prometheus target/rule identities without duplicate scrape targets;
- endpoint health contracts produce Gatus checks that target the component endpoint;
- log contracts have stable Loki labels keyed by host/service/component;
- Grafana queries refer to the same labels and metric identities;
- a required edge failure rolls up to the source service only.

**Step 2: Run tests to verify they fail**

Run the owning repository's focused generator tests.

Expected: FAIL until the projection layer accepts v2 component-owned contracts.

**Step 3: Implement projection adapters**

Implement version-aware adapters from the v2 plan; keep existing v1 generators intact. Deduplicate only by stable planned identity, never by human display name. Preserve the old RelayOS monitor configuration until generated output and live evidence are reviewed.

**Step 4: Verify live evidence without deployment mutation**

After the generated monitoring configuration is safely deployed through normal GitOps, query the existing Prometheus and Gatus APIs. Capture component-level evidence and compare it to the legacy endpoints. Any missing data is \`unknown\`, not a pass.

**Step 5: Open a reviewable PR**

The PR must link #411, include static plan output, observer projection diff, live evidence timestamps, legacy-check parity, and an explicit statement that no RelayOS Compose service was recreated.

---

### Task 7: Enable typed contracts and retire duplicate legacy declarations only after soak

**Files:**
- Modify: canonical registry/management sources identified in Tasks 5 and 6
- Modify: legacy RelayOS observation declarations only after their typed equivalent has passed review

**Step 1: Define acceptance evidence before removal**

Require a continuous soak window agreed on #411 with:

- successful \`doctor host --validate\` static output;
- fresh, healthy component and edge evidence from Prometheus/Gatus;
- no divergence from legacy service availability checks;
- no unexpected target cardinality increase;
- no RelayOS runtime/container change in reconcile evidence.

**Step 2: Remove one duplicated legacy declaration at a time**

For each retirement, add a test proving the typed projection remains, regenerate configurations, and show which old check is removed. Never batch-remove all legacy checks based only on static validity.

**Step 3: Verify after each removal**

Run the focused generation tests, then query live Gatus and Prometheus through the documented read-only APIs. Stop and restore the last known-good declaration through GitOps if typed evidence diverges.

**Step 4: Final verification**

\`\`\`bash
.venv/bin/python scripts/check_docs.py
.venv/bin/python -m ruff format --check src tests scripts
.venv/bin/python -m ruff check src tests scripts
.venv/bin/python -m mypy src scripts
.venv/bin/python scripts/generate_observation_schemas.py
.venv/bin/python -m pytest
git diff --check
\`\`\`

Expected: all checks pass; schemas are current; only approved projection/registry changes remain.

---

## Review checklist

- [ ] v1 documents and schemas are unchanged and remain readable.
- [ ] v2 component identity is stable, qualified, deterministic, and not inferred from display text.
- [ ] Resources are typed and secret-free in all diagnostics, plans, schemas, and examples.
- [ ] Doctor readiness is generic and non-mutating; it distinguishes invalid, unknown, and observed-unhealthy states.
- [ ] RelayOS declaration changes do not change Compose, images, secrets, listeners, firewall, or host paths.
- [ ] Prometheus, Gatus, Loki, Grafana, and Doctor derive from one typed contract rather than duplicated hand-maintained declarations.
- [ ] Legacy checks are retired only after measured parity and a documented soak window.
