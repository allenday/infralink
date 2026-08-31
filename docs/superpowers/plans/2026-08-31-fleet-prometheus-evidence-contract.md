# Fleet Prometheus Evidence Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish a strict, non-runnable `infralink.fleet-prometheus-evidence/v1` contract that independent Registry, controller, and later public-reader work can share.

**Architecture:** The Infralink Python library owns strict artifact models, canonical signing bytes, a distributable JSON Schema, and fixtures. This commits no CLI, MCP, Prometheus client, credential resolution, artifact-path option, or repair behavior. `infralink-ops` will consume the released contract for production/signing after it is published; the Registry will independently declare the stable target IDs and binding references.

**Tech Stack:** Python 3.12, Pydantic v2, JSON Schema Draft 2020-12, pytest.

---

### Task 1: Publish strict contract models and canonical payload bytes

**Files:**
- Create: `src/infralink/fleet/prometheus_evidence.py`
- Modify: `src/infralink/fleet/__init__.py`
- Test: `tests/test_fleet_prometheus_evidence.py`

- [ ] **Step 1: Write failing model and canonicalization tests**

```python
def test_valid_fixture_has_stable_unsigned_canonical_payload() -> None:
    evidence = FleetPrometheusEvidence.model_validate(load_fixture("valid.json"))
    assert evidence.canonical_signed_bytes() == load_fixture_bytes("unsigned.json")


def test_target_status_and_detail_code_pairs_fail_closed() -> None:
    payload = load_fixture("valid.json")
    payload["targets"][0]["detail_code"] = "query_timeout"
    with pytest.raises(ValidationError):
        FleetPrometheusEvidence.model_validate(payload)
```

- [ ] **Step 2: Run the focused test to verify it fails**

Run: `pytest tests/test_fleet_prometheus_evidence.py -v`
Expected: FAIL because the evidence module and fixtures do not exist.

- [ ] **Step 3: Implement strict models**

```python
class FleetPrometheusEvidence(ContractModel):
    schema_version: Literal["infralink.fleet-prometheus-evidence/v1"]
    registry_revision: str
    generated_at: datetime
    window_seconds: int
    targets: tuple[FleetPrometheusTarget, ...]
    signature: FleetPrometheusEvidenceSignature

    def canonical_signed_bytes(self) -> bytes:
        payload = self.model_dump(mode="json")
        payload["signature"].pop("value")
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
```

Reject unexpected fields, non-UTC timestamps, duplicate IDs, out-of-bounds
windows, and invalid status/detail/timestamp combinations.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_fleet_prometheus_evidence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infralink/fleet tests/test_fleet_prometheus_evidence.py
git commit -m "feat: add fleet Prometheus evidence contract"
```

### Task 2: Ship a language-neutral schema and cross-check fixtures

**Files:**
- Create: `src/infralink/schemas/fleet/prometheus-evidence-v1.json`
- Create: `tests/fixtures/fleet-prometheus-evidence/valid.json`
- Create: `tests/fixtures/fleet-prometheus-evidence/unsigned.json`
- Modify: `tests/test_fleet_prometheus_evidence.py`

- [ ] **Step 1: Write a failing JSON Schema fixture test**

```python
def test_json_schema_accepts_the_shared_fixture_and_rejects_extra_fields() -> None:
    validator = Draft202012Validator(load_schema())
    assert list(validator.iter_errors(load_fixture("valid.json"))) == []
    invalid = load_fixture("valid.json")
    invalid["unexpected"] = True
    assert list(validator.iter_errors(invalid))
```

- [ ] **Step 2: Run focused test to verify it fails**

Run: `pytest tests/test_fleet_prometheus_evidence.py::test_json_schema_accepts_the_shared_fixture_and_rejects_extra_fields -v`
Expected: FAIL because the distributable schema and fixture do not exist.

- [ ] **Step 3: Add the Draft 2020-12 schema and deterministic fixtures**

The schema must forbid unknown fields, bound all strings/arrays/integers, and
encode every possible status/detail pairing. The valid fixture must include one
`observed`, one `absent`, and one `query_error` target. `unsigned.json` must be
the exact canonical bytes of that fixture with only `signature.value` omitted.

- [ ] **Step 4: Run focused tests**

Run: `pytest tests/test_fleet_prometheus_evidence.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/infralink/schemas/fleet tests/fixtures/fleet-prometheus-evidence tests/test_fleet_prometheus_evidence.py
git commit -m "test: add fleet evidence schema fixtures"
```

### Task 3: Verify release contents and no public surface expansion

**Files:**
- Modify: `tests/test_fleet_prometheus_evidence.py`
- Modify: `docs/superpowers/specs/2026-08-31-fleet-prometheus-evidence-design.md`

- [ ] **Step 1: Write failing package/surface tests**

```python
def test_contract_schema_is_included_in_the_wheel() -> None:
    assert (ROOT / "src/infralink/schemas/fleet/prometheus-evidence-v1.json").is_file()


def test_contract_adds_no_click_or_agent_surface_command() -> None:
    source = (ROOT / "src/infralink/fleet/prometheus_evidence.py").read_text()
    assert "@click" not in source
    assert "@operator_surface" not in source
```

- [ ] **Step 2: Run tests to verify they fail before the contract exists**

Run: `pytest tests/test_fleet_prometheus_evidence.py -v`
Expected: FAIL until Tasks 1 and 2 are implemented.

- [ ] **Step 3: Record the release handoff**

Document that Registry and Ops consume the exact `v1` schema after the contract
release, and that Infralink #306 remains blocked from adding `--live` behavior
until their artifact shape is stable.

- [ ] **Step 4: Run targeted and full static checks**

Run: `pytest tests/test_fleet_prometheus_evidence.py tests/test_fleet_validation.py -v && ruff check src/infralink/fleet tests/test_fleet_prometheus_evidence.py`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/specs tests/test_fleet_prometheus_evidence.py
git commit -m "docs: record fleet evidence contract handoff"
```
