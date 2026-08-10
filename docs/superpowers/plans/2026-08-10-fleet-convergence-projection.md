# Fleet Convergence Projection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Project each active V2 host's bounded local Doctor result into generated Prometheus, Grafana, and Gatus fleet-convergence evidence.

**Architecture:** Extend the existing local Doctor branch, not the public release branch, with typed check severity and safe metric exposition. The host-local agent evaluates live baseline, reconciliation, registry-layout, and firewall state. `infra-registry` declares the local Doctor as an observation profile and deterministically generates all Prometheus, Grafana, and aggregate-only Gatus artifacts; Grafana is never written through its API.

**Tech Stack:** Python 3.12, Pydantic contracts, systemd, Node Exporter textfile metrics, Prometheus, Gatus, Grafana provisioning, typed YAML registry documents.

---

### Task 1: Typed Local Doctor Findings and Firewall Evidence

**Files:**
- Modify: `src/infralink/local_doctor.py`
- Modify: `src/infralink/host_readiness.py`
- Modify: `src/infralink/host_transport.py`
- Modify: `tests/test_local_doctor.py`
- Modify: `tests/test_host_readiness.py`

- [ ] **Step 1: Write failing local-Doctor tests**

Add tests that construct simultaneous required errors and warnings, then assert each serialized check has only `id`, `required`, `passed`, `severity`, and `detail_code`; add tests for a V2 host whose nftables probe is lax and whose result is not converged.

- [ ] **Step 2: Run the focused tests to verify failure**

Run: `PYTHONPATH=src python3 -m pytest -q tests/test_local_doctor.py tests/test_host_readiness.py`

Expected: failure because `severity`, `detail_code`, and the firewall check do not exist.

- [ ] **Step 3: Implement the closed finding contract**

Add a closed severity enum `error|warning|info`, fixed detail codes, and a required `firewall_converged` readiness check. The probe must only classify the declared default-deny/SSH/service-ingress state; it must retain no raw nftables rules, addresses, paths, command output, or secrets.

- [ ] **Step 4: Verify focused tests pass**

Run: `PYTHONPATH=src python3 -m pytest -q tests/test_local_doctor.py tests/test_host_readiness.py`

Expected: PASS.

- [ ] **Step 5: Commit the focused change**

Commit message: `feat(doctor): classify typed local convergence findings`

### Task 2: Bounded Prometheus Projection from Local Doctor

**Files:**
- Modify: `src/infralink/local_doctor.py`
- Modify: `tests/test_local_doctor.py`

- [ ] **Step 1: Write failing metric tests**

Add tests for a stable text exposition containing exactly `infralink_doctor_converged`, `infralink_doctor_result_age_seconds`, and `infralink_doctor_check_passed`. Assert only host UUID, fixed check ID, `required`, and `severity` labels; assert no display name, IP, detail, path, or secret appears.

- [ ] **Step 2: Run the targeted metric tests to verify failure**

Run: `PYTHONPATH=src python3 -m pytest -q tests/test_local_doctor.py -k 'metric or prometheus'`

Expected: failure because the bounded exposition endpoint is absent.

- [ ] **Step 3: Implement deterministic metric rendering**

Render metrics from the persisted local Doctor result only. A stale or malformed result must expose aggregate non-convergence and no arbitrary labels. Keep `/v1/doctor/latest` semantics: fresh healthy returns 200; stale or non-converged returns 503.

- [ ] **Step 4: Verify local agent tests pass**

Run: `PYTHONPATH=src python3 -m pytest -q tests/test_local_doctor.py`

Expected: PASS.

- [ ] **Step 5: Commit the metric change**

Commit message: `feat(doctor): expose bounded convergence metrics`

### Task 3: Package and Schedule the Local Agent in V2 Runtime

**Files:**
- Modify: `ansible/tasks/self_deploy_v2_runtime.yml` in `infra-management`
- Modify: `ansible/playbooks/gitops_bootstrap.yml` in `infra-management`
- Create: `systemd/infralink-local-doctor.service` in `infra-management`
- Create: `systemd/infralink-local-doctor.timer` in `infra-management`
- Test: `scripts/tests/test_self_deploy_v2_runtime_packaging.py` in `infra-management`

- [ ] **Step 1: Write failing runtime-contract tests**

Add tests that require a root-owned one-shot collector timer, a static Tailnet-bound HTTP listener, persistence under `/var/lib/infralink/doctor`, and a Node Exporter textfile output. Assert no Compose service, no direct Grafana API invocation, and no secret-bearing environment propagation.

- [ ] **Step 2: Run the focused runtime tests to verify failure**

Run: `python3 -m pytest -q scripts/tests/test_self_deploy_v2_runtime_packaging.py`

Expected: failure because the local Doctor units are absent from the exact runtime payload.

- [ ] **Step 3: Package the minimal systemd runtime**

Install the exact Infralink local Doctor executable, collector timer, HTTP service, and Node Exporter textfile path through the V2 runtime. Bind only the declared Tailnet address and let the existing declared firewall render the allowed Watchtower access.

- [ ] **Step 4: Verify runtime contract tests pass**

Run: `python3 -m pytest -q scripts/tests/test_self_deploy_v2_runtime_packaging.py`

Expected: PASS.

- [ ] **Step 5: Commit the runtime packaging change**

Commit message: `feat(self-deploy): package local doctor convergence agent`

### Task 4: Registry-Derived Fleet Observation Artifacts

**Files:**
- Modify: `service-catalog/profiles/observability.yml` in `infra-registry`
- Create: `service-catalog/instances/<host-uuid>.yml` additions in `infra-registry`
- Modify: `operations/observation/views.yml` in `infra-registry`
- Modify: `operations/observation/readiness-suites.yml` in `infra-registry`
- Modify: generated `operations/observation/rendered/{prometheus,gatus,grafana}` in `infra-registry`
- Test: registry observation generator and rendered-artifact tests

- [ ] **Step 1: Write failing registry projection tests**

Require exactly one UUID-keyed `local-doctor` instance for each active V2 host, a Prometheus metric scrape, one aggregate-only Gatus endpoint per host using `/v1/doctor/latest`, and a generated `fleet-convergence` Grafana view with typed check breakdown. Assert no dashboard is hand-authored and no Gatus endpoint represents individual checks.

- [ ] **Step 2: Run focused registry tests to verify failure**

Run the registry observation validation and affected generator tests.

Expected: failure because the local Doctor profile and generated fleet view do not yet exist.

- [ ] **Step 3: Add typed observations and regenerate artifacts**

Declare the profile endpoints and capabilities in typed YAML, add active V2 host instances by UUID, extend the fleet view/readiness suite, and regenerate the checked Prometheus, Gatus, and Grafana artifacts through the existing renderer. Preserve current core dashboard semantics.

- [ ] **Step 4: Verify deterministic registry output**

Run the registry's normal observation validator, renderer byte-comparison, and focused generated-artifact tests twice.

Expected: PASS and identical generated output both times.

- [ ] **Step 5: Commit the registry projection**

Commit message: `feat(observation): project fleet local doctor convergence`

### Task 5: Three-Host V2 Canary and Evidence

**Files:**
- Modify: host deployment declarations in `infra-registry` only as required for V2 local-agent enablement
- Create: redacted canary evidence under `docs/evidence/` in `infra-registry`

- [ ] **Step 1: Confirm the signed V2 candidate and bootstrap readiness**

Use `infralink doctor host` for `cyberstorm-watchtower`, `cyberstorm-citadel`, and `relaxgg-db-es1`. Confirm V2 runtime, safe registry layout, declared firewall policy, and no competing legacy mutation authority before enabling the local-agent timer.

- [ ] **Step 2: Apply only the generated V2 declaration**

Use the encapsulated host apply/bootstrap path. Do not run legacy `self-deploy.sh`, manually create dashboards, or alter nftables outside the declared renderer.

- [ ] **Step 3: Verify all evidence surfaces**

Verify each host's fresh local Doctor endpoint and bounded metrics, Prometheus scrape, generated Grafana fleet-convergence dashboard, and aggregate Gatus result. Introduce one reversible non-service local probe fault in a canary fixture to prove a required failure drives aggregate non-convergence; restore it and record only redacted evidence.

- [ ] **Step 4: Commit evidence and close the issue**

Commit message: `docs(evidence): verify fleet local doctor convergence canary`

Close `relaxgg/infra-registry#287` only after all three hosts converge and generated Grafana/Gatus evidence is observed.
