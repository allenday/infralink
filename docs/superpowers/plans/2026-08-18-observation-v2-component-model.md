# Observation V2 Component Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a strict, additive `infralink.observation/v2` source model for service components, component endpoints, and endpoint-to-endpoint edges without changing v1 planning, renderers, controller behavior, or host state.

**Architecture:** V1 remains unchanged. The loader accepts a bounded set of explicit schema versions and retains each document's version. V2 models own a service profile's component slots and endpoint contracts, instantiate those slots on a host, and use a canonical four-segment endpoint reference (`host/service/component/endpoint`) for edges. Edge scope is a derived property of the two endpoint owners, never an authored field.

**Tech Stack:** Python 3.10+, Pydantic v2, PyYAML, pytest.

---

## Boundaries

- V2 source documents may coexist in the loader with V1 documents, but V1's planner still rejects V2 input. Cross-version aggregation is a later Doctor/query concern.
- No migration of registry documents, Compose, secrets, Prometheus, Gatus, Grafana, controller, or host artifact is included.
- Component resource slots and metric contracts are explicitly deferred to #170 and #171.
- Endpoint existence/reference resolution against the complete document is deferred to the V2 planner slice. This slice validates canonical endpoint reference shape and derives ownership only.

## V2 Source Shape

```yaml
schema_version: infralink.observation/v2
service_profiles:
  - id: proxied-elasticsearch
    components:
      - id: elasticsearch
        endpoints:
          - {id: transport, protocol: tcp, port: 9300}
      - id: nginx
        endpoints:
          - {id: http, protocol: http, port: 9200}
service_instances:
  - id: archive
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: proxied-elasticsearch
    components:
      - slot_id: elasticsearch
      - slot_id: nginx
component_edges:
  - id: nginx-to-elasticsearch
    source_endpoint_id: 11111111-1111-4111-8111-111111111111/archive/nginx/http
    target_endpoint_id: 11111111-1111-4111-8111-111111111111/archive/elasticsearch/transport
```

### Task 1: Make source loading version-aware

**Files:**
- Modify: `src/infralink/observation/loader.py`
- Modify: `tests/observation/test_loader.py`

- [ ] **Step 1: Write the failing loader test**

```python
def test_loader_accepts_v1_and_v2_documents_without_coercion(tmp_path: Path) -> None:
    _write(tmp_path / "v1.yml", "schema_version: infralink.observation/v1\n")
    _write(tmp_path / "v2.yml", "schema_version: infralink.observation/v2\n")

    report = load_observation_documents(tmp_path)

    assert report.valid
    assert [document.schema_version for document in report.documents] == [
        "infralink.observation/v1",
        "infralink.observation/v2",
    ]
```

- [ ] **Step 2: Run the test and verify it fails because v2 is unsupported**

Run: `python -m pytest --no-cov -q tests/observation/test_loader.py::test_loader_accepts_v1_and_v2_documents_without_coercion`

Expected: failure containing `schema-version-unsupported`.

- [ ] **Step 3: Add the minimum version boundary**

Replace the singleton source-version constant with `SUPPORTED_SCHEMA_VERSIONS = frozenset({"infralink.observation/v1", "infralink.observation/v2"})`. Add `schema_version: str` to `ObservationDocument`; preserve the parsed document mapping exactly. Reject all versions outside that set with the existing typed diagnostic, naming the supported versions in the next action.

- [ ] **Step 4: Run the focused loader tests**

Run: `python -m pytest --no-cov -q tests/observation/test_loader.py`

Expected: all loader tests pass.

- [ ] **Step 5: Commit the isolated loader change**

```bash
git add src/infralink/observation/loader.py tests/observation/test_loader.py
git commit -m "feat(observation): accept versioned v2 source documents"
```

### Task 2: Define strict V2 component and endpoint source models

**Files:**
- Create: `src/infralink/observation/models_v2.py`
- Modify: `tests/observation/test_models.py`

- [ ] **Step 1: Write failing model tests for component identity and edge scope**

```python
def test_component_edge_derives_intra_service_scope() -> None:
    edge = ComponentEdge(
        id="nginx-to-elasticsearch",
        source_endpoint_id="11111111-1111-4111-8111-111111111111/archive/nginx/http",
        target_endpoint_id="11111111-1111-4111-8111-111111111111/archive/elasticsearch/transport",
    )

    assert edge.scope is EdgeScope.INTRA_SERVICE


def test_component_edge_rejects_noncanonical_endpoint_reference() -> None:
    with pytest.raises(ValidationError, match="host/service/component/endpoint"):
        ComponentEdge(id="bad", source_endpoint_id="archive/http", target_endpoint_id="a/b/c/d")
```

- [ ] **Step 2: Run the tests and verify import failure**

Run: `python -m pytest --no-cov -q tests/observation/test_models.py -k 'component_edge'`

Expected: failure because `ComponentEdge` is not importable.

- [ ] **Step 3: Implement only the V2 source types**

In `models_v2.py`, reuse V1's `CanonicalId`, `Endpoint`, `HostId`, `QualifiedRef`, and `StrictModel`:

```python
class ComponentSlot(StrictModel):
    id: CanonicalId
    endpoints: Annotated[list[Endpoint], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicate_endpoint_ids(self) -> ComponentSlot: ...


class ComponentInstance(StrictModel):
    slot_id: CanonicalId


class ServiceProfileV2(StrictModel):
    id: CanonicalId
    components: Annotated[list[ComponentSlot], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicate_component_ids(self) -> ServiceProfileV2: ...


class ServiceInstanceV2(StrictModel):
    id: CanonicalId
    host_id: HostId
    profile_id: CanonicalId
    components: Annotated[list[ComponentInstance], Field(min_length=1)]

    @model_validator(mode="after")
    def reject_duplicate_slot_bindings(self) -> ServiceInstanceV2: ...
```

Implement `parse_component_endpoint_ref(value: str) -> tuple[str, str, str, str]`. It must require four slash-separated canonical identifiers, validate the first segment through `HostId`, and reject any noncanonical spelling. Use it from `ComponentEdge` to expose immutable `source_owner` and `target_owner` tuples plus a derived `scope: EdgeScope` enum. `ComponentEdge` must not accept an authored `scope` field.

- [ ] **Step 4: Run model tests and static checks**

Run: `python -m pytest --no-cov -q tests/observation/test_models.py && python -m ruff check src/infralink/observation/models_v2.py tests/observation/test_models.py && python -m mypy src/infralink/observation/models_v2.py`

Expected: all pass.

- [ ] **Step 5: Commit the V2 model boundary**

```bash
git add src/infralink/observation/models_v2.py tests/observation/test_models.py
git commit -m "feat(observation): define v2 component endpoint model"
```

### Task 3: Parse a complete V2 source document without invoking the V1 planner

**Files:**
- Create: `src/infralink/observation/v2.py`
- Create: `tests/observation/test_v2.py`
- Modify: `src/infralink/observation/__init__.py`

- [ ] **Step 1: Write the failing V2-document test**

```python
def test_parse_v2_document_keeps_component_endpoint_edges_typed() -> None:
    parsed = parse_v2_document(
        {
            "schema_version": "infralink.observation/v2",
            "service_profiles": [{"id": "proxy", "components": [...]}],
            "service_instances": [{"id": "api", "host_id": HOST_ID, "profile_id": "proxy", "components": [...]}],
            "component_edges": [{"id": "proxy-to-app", "source_endpoint_id": ..., "target_endpoint_id": ...}],
        }
    )

    assert parsed.component_edges[0].scope is EdgeScope.INTRA_SERVICE
```

- [ ] **Step 2: Run the test and verify import failure**

Run: `python -m pytest --no-cov -q tests/observation/test_v2.py`

Expected: failure because `parse_v2_document` is not importable.

- [ ] **Step 3: Implement the strict document model and parser**

Create `ObservationV2Document(StrictModel)` with literal schema version, `service_profiles`, `service_instances`, and `component_edges`, all defaulting to empty lists. `parse_v2_document(data)` validates at the JSON boundary with `ObservationV2Document.model_validate_json(json.dumps(data))`, matching the strict v1 planner boundary: YAML source strings must become typed enum values without weakening strict in-memory validation. Export `ObservationV2Document` and `parse_v2_document` from `infralink.observation`.

Do not add the V2 model to `resolve_observation_documents`, `Plan`, or any renderer in this task. That protects V1 semantics and makes the later V2 planner/projection boundary explicit.

- [ ] **Step 4: Run V2 and regression tests**

Run: `python -m pytest --no-cov -q tests/observation/test_loader.py tests/observation/test_models.py tests/observation/test_v2.py tests/observation/test_planner.py`

Expected: all pass; the existing planner test continues to reject V2 documents.

- [ ] **Step 5: Commit and open the implementation PR**

```bash
git add src/infralink/observation/__init__.py src/infralink/observation/v2.py tests/observation/test_v2.py
git commit -m "feat(observation): parse v2 component source documents"
git push -u cyberstorm feat/issue169-component-model
gh pr create --repo cyberstorm-dev/infralink --base main --head feat/issue169-component-model --title "feat(observation): define v2 component endpoint model" --body "Closes #169"
```

## Verification

- [ ] `python -m pytest --no-cov -q tests/observation/test_loader.py tests/observation/test_models.py tests/observation/test_v2.py tests/observation/test_planner.py`
- [ ] `python -m ruff check src/infralink/observation tests/observation`
- [ ] `python -m mypy src/infralink/observation`
- [ ] Independent spec and code-quality review before merge.
