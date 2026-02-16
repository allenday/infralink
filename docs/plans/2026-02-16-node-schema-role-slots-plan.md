# NodeSchema + Role Slots Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add an abstract `NodeSchema`, typed required-by-default role slots, and validation for node-type edge pairs while keeping registry layout stable.

**Architecture:** Introduce `NodeSchema` as the base for `HostSchema` and `ServiceSchema` with a `node_type` discriminator. Extend `RoleConfig` with typed `slots` and add slot bindings in role overrides. Add validation for node-type edge pairs and a helper to validate slot bindings against a role catalog.

**Tech Stack:** Python, Pydantic v2, pytest, infralink core schema/registry/CLI.

---

### Task 1: Add failing tests for NodeSchema defaults + inheritance

**Files:**
- Create: `tests/test_node_schema.py`

**Step 1: Write the failing test**
```python
from infralink.core.schema import HostSchema, ServiceSchema

def test_host_schema_defaults_node_type():
    host = HostSchema(canonical_name="h1")
    assert host.node_type == "host"


def test_service_schema_defaults_node_type_and_canonical_name():
    service = ServiceSchema(name="postgres", group="db")
    assert service.node_type == "service"
    assert service.canonical_name == "postgres"
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_node_schema.py -v`
Expected: FAIL with missing `node_type`/`canonical_name` behavior.

**Step 3: Write minimal implementation**
- Add `NodeSchema` base class with `node_type` + shared fields.
- Make `HostSchema(NodeSchema)` default `node_type="host"`.
- Make `ServiceSchema(NodeSchema)` default `node_type="service"` and set `canonical_name` from `name` if missing.

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_node_schema.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_node_schema.py src/infralink/core/schema.py
git commit -m "feat: add NodeSchema base with node_type defaults"
```

---

### Task 2: Add failing tests for role slot definitions + bindings

**Files:**
- Modify: `tests/test_node_schema.py`

**Step 1: Write the failing test**
```python
from infralink.core.schema import RoleConfig, SlotConfig, SlotBinding

def test_role_slots_required_by_default():
    role = RoleConfig(services={}, slots={"db": {"type": "database"}})
    assert role.slots["db"].required is True


def test_slot_binding_schema():
    binding = SlotBinding(host="h1", service="postgres", role="rw")
    assert binding.host == "h1"
    assert binding.service == "postgres"
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_node_schema.py -v`
Expected: FAIL because SlotConfig/SlotBinding don’t exist.

**Step 3: Write minimal implementation**
- Add `SlotConfig` and `SlotBinding` models.
- Extend `RoleConfig` with `slots: dict[str, SlotConfig]`.

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_node_schema.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_node_schema.py src/infralink/core/schema.py
git commit -m "feat: add role slot schema and bindings"
```

---

### Task 3: Add failing tests for role slot binding validation helper

**Files:**
- Create: `tests/test_role_slot_validation.py`
- Modify: `src/infralink/core/registry.py` or new helper module

**Step 1: Write the failing test**
```python
from infralink.core.schema import RoleConfig, HostSchema, SlotBinding
from infralink.core.registry import validate_role_slots


def test_validate_role_slots_requires_binding():
    roles = {"wordpress": RoleConfig(services={}, slots={"db": {"type": "database"}})}
    hosts = {
        "uuid": HostSchema(canonical_name="h1", roles=["wordpress"], role_overrides={"wordpress": {}})
    }
    errors = validate_role_slots(hosts, roles)
    assert any("slot db" in e for e in errors)
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_role_slot_validation.py -v`
Expected: FAIL because helper doesn’t exist.

**Step 3: Write minimal implementation**
- Add `validate_role_slots(hosts: dict[str, HostSchema], roles: dict[str, RoleConfig]) -> list[str]`.
- Enforce required slots and binding targets (host+service keys).

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_role_slot_validation.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_role_slot_validation.py src/infralink/core/registry.py src/infralink/core/schema.py
git commit -m "feat: validate role slot bindings"
```

---

### Task 4: Add failing tests for node-type edge validation in CLI

**Files:**
- Create: `tests/test_edge_node_type_validation.py`
- Modify: `src/infralink/cli/validate.py`

**Step 1: Write the failing test**
```python
from infralink.cli.validate import _edge_node_type_error

def test_edge_node_type_service_to_service_ok():
    assert _edge_node_type_error(source_service="app") is None


def test_edge_node_type_host_to_service_ok():
    assert _edge_node_type_error(source_service=None) is None
```

**Step 2: Run test to verify it fails**
Run: `pytest tests/test_edge_node_type_validation.py -v`
Expected: FAIL because helper doesn’t exist.

**Step 3: Write minimal implementation**
- Add `_edge_node_type_error` helper to `cli/validate.py`.
- Hook into edge validation loop to add error when pair invalid (future‑proofed for other node types).

**Step 4: Run test to verify it passes**
Run: `pytest tests/test_edge_node_type_validation.py -v`
Expected: PASS

**Step 5: Commit**
```bash
git add tests/test_edge_node_type_validation.py src/infralink/cli/validate.py
git commit -m "feat: validate edge node-type pairs"
```

---

### Task 5: Run full test suite

**Step 1: Run tests**
Run: `pytest -v`
Expected: PASS

**Step 2: Commit (if any fixes)**
```bash
git add -A
git commit -m "test: keep suite green"
```

---

Plan complete and saved to `docs/plans/2026-02-16-node-schema-role-slots-plan.md`.

Two execution options:
1. Subagent-Driven (this session) – dispatch fresh subagent per task, review between tasks
2. Parallel Session (separate) – execute plan with checkpoints via executing-plans

Which approach?
