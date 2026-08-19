from __future__ import annotations

import builtins
import json
import socket
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path

import pytest

AS_OF = datetime(2026, 8, 4, tzinfo=timezone.utc)


class _IndeterminateTimezone(tzinfo):
    def utcoffset(self, dt: datetime | None) -> timedelta | None:
        return None

    def dst(self, dt: datetime | None) -> timedelta | None:
        return None

    def tzname(self, dt: datetime | None) -> str | None:
        return "indeterminate"


def _write_contract(path: Path, *, comment: str = "") -> None:
    path.write_text(
        comment
        + """schema_version: infralink.observation/v1
hosts:
  - id: 11111111-1111-4111-8111-111111111111
service_profiles:
  - id: web
    endpoints:
      - id: http
        protocol: http
        port: 8080
    health:
      - id: ready
        endpoint_id: http
        evaluator: http-status
    signals:
      - id: up
        capability_id: ready
        evaluator: capability-state
service_instances:
  - id: api
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: web
""",
        encoding="utf-8",
    )


def test_public_api_imports_without_click_or_provider_modules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name == "click" or name.startswith(("infralink.cli", "infralink.adapters")):
            raise AssertionError(f"forbidden public API import: {name}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    from infralink.observation import project, project_v2_metric_contracts, validate

    assert callable(project)
    assert callable(project_v2_metric_contracts)
    assert callable(validate)


def test_public_api_projects_split_v2_metric_contracts_with_source_provenance(
    tmp_path: Path,
) -> None:
    from infralink.observation import project_v2_metric_contracts

    profiles = tmp_path / "profiles.yml"
    profiles.write_text(
        """schema_version: infralink.observation/v2
service_profiles:
  - id: nginx
    components:
      - id: exporter
        endpoints:
          - {id: metrics, protocol: http, port: 9113}
        metrics:
          - id: requests
            endpoint_id: metrics
            path: /metrics
            metric_name: nginx_http_requests_total
            unit: requests
            allowed_labels: [environment]
            health_query: sum(nginx_http_requests_total)
            condition: {operator: gte, threshold: 0}
            readiness_required: true
""",
        encoding="ascii",
    )
    instances = tmp_path / "instances.yml"
    instances.write_text(
        """schema_version: infralink.observation/v2
service_instances:
  - id: nginx
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: nginx
    components:
      - slot_id: exporter
        endpoint_bindings:
          - {endpoint_id: metrics, address: 100.64.0.10}
        metric_bindings:
          - metric_id: requests
            labels: {environment: production}
""",
        encoding="ascii",
    )

    result = project_v2_metric_contracts([profiles, instances])

    assert [item.id for item in result.metrics] == [
        "11111111-1111-4111-8111-111111111111/nginx/exporter/requests"
    ]
    assert result.metrics[0].prometheus.address == "100.64.0.10"
    assert result.metrics[0].doctor.required is True
    assert [source.path for source in result.sources] == ["instances.yml", "profiles.yml"]


def test_public_api_rejects_non_v2_metric_source(tmp_path: Path) -> None:
    from infralink.observation import ProjectValidationError, project_v2_metric_contracts

    source = tmp_path / "legacy.yml"
    _write_contract(source)

    with pytest.raises(ProjectValidationError) as caught:
        project_v2_metric_contracts([source])

    assert [item.code for item in caught.value.report.diagnostics] == [
        "v2-metric-source-version-invalid"
    ]


def test_public_api_rejects_empty_v2_metric_source_set(tmp_path: Path) -> None:
    from infralink.observation import ProjectValidationError, project_v2_metric_contracts

    with pytest.raises(ProjectValidationError) as caught:
        project_v2_metric_contracts([tmp_path])

    assert [item.code for item in caught.value.report.diagnostics] == [
        "no-usable-v2-metric-document"
    ]


def test_public_api_projects_same_instance_key_on_distinct_hosts(tmp_path: Path) -> None:
    from infralink.observation import project, validate

    source = tmp_path / "contract.yml"
    source.write_text(
        """schema_version: infralink.observation/v1
hosts:
  - id: 11111111-1111-4111-8111-111111111111
  - id: 22222222-2222-4222-8222-222222222222
service_profiles:
  - id: node-exporter
    endpoints: []
    health: []
    metrics: []
    logs: []
    signals: []
    secret_slots: []
service_instances:
  - id: node-exporter
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: node-exporter
  - id: node-exporter
    host_id: 22222222-2222-4222-8222-222222222222
    profile_id: node-exporter
""",
        encoding="ascii",
    )

    report = validate([source], as_of=AS_OF)
    result = project([source], as_of=AS_OF)

    assert report.valid
    assert [service.id for service in result.plan.services] == [
        "11111111-1111-4111-8111-111111111111/node-exporter",
        "22222222-2222-4222-8222-222222222222/node-exporter",
    ]


def test_bounded_validation_prioritizes_malformed_instance_host_diagnostic(
    tmp_path: Path,
) -> None:
    from infralink.observation import validate

    source = tmp_path / "invalid.yml"
    source.write_text(
        """schema_version: infralink.observation/v1
service_profiles:
  - id: node-exporter
    endpoints: []
    health: []
    metrics: []
    logs: []
    signals: []
    secret_slots: []
service_instances:
  - id: node-exporter
    profile_id: node-exporter
  - id: node-exporter
    host_id: [not-a-host]
    profile_id: node-exporter
""",
        encoding="ascii",
    )

    report = validate([source], limit=1, as_of=AS_OF)

    assert not report.valid
    assert report.diagnostics.total_count == 2
    assert [item.code for item in report.diagnostics] == ["invalid-document-record"]
    assert report.diagnostics[0].location.pointer == "/service_instances/1/host_id"


def test_validate_aggregates_model_errors_and_is_bounded(tmp_path: Path) -> None:
    from infralink.observation import validate

    source = tmp_path / "invalid.yml"
    source.write_text(
        """schema_version: infralink.observation/v1
hosts:
  - id: not-a-uuid
service_instances:
  - id: api
    host_id: not-a-uuid
    profile_id: missing
""",
        encoding="utf-8",
    )

    report = validate([source], limit=1, as_of=AS_OF)

    assert not report.valid
    assert report.diagnostics.total_count >= 2
    assert report.diagnostics.truncated
    assert len(report.diagnostics) == 1


def test_validate_aggregates_loader_and_planner_errors_under_one_global_limit(
    tmp_path: Path,
) -> None:
    from infralink.observation import ProjectValidationError, project, validate

    malformed = tmp_path / "a.yml"
    malformed.write_text("schema_version: [broken\n", encoding="utf-8")
    model_invalid = tmp_path / "b.yml"
    model_invalid.write_text(
        """schema_version: infralink.observation/v1
hosts:
  - id: not-a-uuid
service_instances:
  - id: api
    host_id: not-a-uuid
    profile_id: missing
""",
        encoding="utf-8",
    )

    report = validate([malformed, model_invalid], limit=2, as_of=AS_OF)

    assert report.document_count == 2
    assert report.diagnostics.total_count == 3
    assert report.diagnostics.truncated
    assert len(report.diagnostics) == 2
    assert {item.code for item in report.diagnostics} == {"invalid-document-record"}
    with pytest.raises(ProjectValidationError) as caught:
        project([malformed, model_invalid], as_of=AS_OF)
    assert {item.code for item in caught.value.report.diagnostics} >= {
        "invalid-document-record",
        "yaml-malformed",
    }


def test_project_returns_plan_and_separate_raw_provenance(tmp_path: Path) -> None:
    from infralink.observation import ProjectResult, project

    source = tmp_path / "contract.yml"
    _write_contract(source)

    result = project([source], registry_revision="registry-7", as_of=AS_OF)

    assert isinstance(result, ProjectResult)
    assert result.plan.schema_version == "infralink.plan.v1"
    assert result.plan.registry_revision == "registry-7"
    assert result.plan.plan_digest is not None
    assert result.plan.compatibility.infralink_schema == "v1"
    assert result.plan.document_digests == (result.sources[0].semantic_sha256,)
    assert len(result.sources[0].raw_sha256) == 64
    assert [item.id for item in result.plan.services] == [
        "11111111-1111-4111-8111-111111111111/api"
    ]


def test_semantic_projection_digest_ignores_yaml_comments(tmp_path: Path) -> None:
    from infralink.observation import project

    first = tmp_path / "first.yml"
    second = tmp_path / "second.yml"
    _write_contract(first)
    _write_contract(second, comment="# formatting-only change\n")

    one = project([first], as_of=AS_OF)
    two = project([second], as_of=AS_OF)

    assert one.plan.document_digests == two.plan.document_digests
    assert one.plan.plan_digest == two.plan.plan_digest
    assert one.sources[0].raw_sha256 != two.sources[0].raw_sha256


def test_declared_input_failures_raise_typed_projection_error(tmp_path: Path) -> None:
    from infralink.observation import ProjectValidationError, project

    source = tmp_path / "invalid.yml"
    source.write_text("schema_version: infralink.observation/v2\n", encoding="utf-8")

    with pytest.raises(ProjectValidationError) as caught:
        project([source], as_of=AS_OF)

    assert {item.code for item in caught.value.report.diagnostics} == {
        "no-usable-observation-document",
        "schema-version-unsupported",
    }


def test_as_of_is_required_by_both_public_operations(tmp_path: Path) -> None:
    from infralink.observation import project, validate

    source = tmp_path / "contract.yml"
    _write_contract(source)

    with pytest.raises(TypeError):
        validate([source])  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        project([source])  # type: ignore[call-arg]


def test_invalid_as_of_is_a_typed_validation_diagnostic(tmp_path: Path) -> None:
    from infralink.observation import ProjectValidationError, project, validate

    source = tmp_path / "contract.yml"
    _write_contract(source)
    naive = datetime(2026, 8, 4)

    report = validate([source], as_of=naive)
    assert report.diagnostics[0].code == "invalid-as-of"
    with pytest.raises(ProjectValidationError) as caught:
        project([source], as_of=naive)
    assert caught.value.report.diagnostics[0].code == "invalid-as-of"


def test_as_of_with_indeterminate_utc_offset_is_typed_invalid(tmp_path: Path) -> None:
    from infralink.observation import ProjectValidationError, project, validate

    source = tmp_path / "contract.yml"
    _write_contract(source)
    indeterminate = datetime(2026, 8, 4, tzinfo=_IndeterminateTimezone())

    assert validate([source], as_of=indeterminate).diagnostics[0].code == "invalid-as-of"
    with pytest.raises(ProjectValidationError) as caught:
        project([source], as_of=indeterminate)
    assert caught.value.report.diagnostics[0].code == "invalid-as-of"


@pytest.mark.parametrize("revision", ["", "   "])
def test_invalid_registry_revision_is_a_typed_projection_failure(
    tmp_path: Path, revision: str
) -> None:
    from infralink.observation import ProjectValidationError, project

    source = tmp_path / "contract.yml"
    _write_contract(source)

    with pytest.raises(ProjectValidationError) as caught:
        project([source], registry_revision=revision, as_of=AS_OF)

    assert caught.value.report.diagnostics[0].code == "invalid-registry-revision"


@pytest.mark.parametrize("revision", ["", "   "])
def test_validate_rejects_invalid_registry_revision(tmp_path: Path, revision: str) -> None:
    from infralink.observation import validate

    source = tmp_path / "source.yml"
    source.write_text("schema_version: infralink.observation/v1\n")

    report = validate([source], as_of=AS_OF, registry_revision=revision)

    assert not report.valid
    assert "invalid-registry-revision" in {item.code for item in report.diagnostics}


def test_validate_enforces_declared_registry_revision(tmp_path: Path) -> None:
    from infralink.observation import validate

    source = tmp_path / "source.yml"
    _write_contract(source)
    text = source.read_text()
    source.write_text("registry_revision: source-7\n" + text)

    report = validate([source], as_of=AS_OF, registry_revision="expected-8")

    assert not report.valid
    assert "registry-revision-conflict" in {item.code for item in report.diagnostics}


@pytest.mark.parametrize("use_empty_directory", [False, True])
def test_empty_inputs_use_typed_no_usable_document_boundary(
    tmp_path: Path, use_empty_directory: bool
) -> None:
    from infralink.observation import ProjectValidationError, project, validate

    paths = [tmp_path] if use_empty_directory else []

    report = validate(paths, as_of=AS_OF)
    assert not report.valid
    assert report.document_count == 0
    assert [item.code for item in report.diagnostics] == ["no-usable-observation-document"]

    with pytest.raises(ProjectValidationError) as caught:
        project(paths, as_of=AS_OF)
    assert caught.value.report.document_count == 0
    assert [item.code for item in caught.value.report.diagnostics] == [
        "no-usable-observation-document"
    ]


def test_public_results_do_not_contain_secret_values(tmp_path: Path) -> None:
    from infralink.observation import project

    source = tmp_path / "contract.yml"
    _write_contract(source)
    serialized = json.dumps(project([source], as_of=AS_OF).to_dict())

    assert "secret-value" not in serialized


def test_public_operations_do_not_access_environment_network_or_providers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from infralink.observation import project, validate

    source = tmp_path / "contract.yml"
    _write_contract(source)

    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("ambient access is forbidden")

    real_import = builtins.__import__

    def guarded_import(
        name: str,
        globals: Mapping[str, object] | None = None,
        locals: Mapping[str, object] | None = None,
        fromlist: Sequence[str] = (),
        level: int = 0,
    ) -> object:
        if name.startswith("infralink.adapters"):
            raise AssertionError("provider imports are forbidden")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("os.environ.get", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(builtins, "__import__", guarded_import)

    assert validate([source], as_of=AS_OF).valid
    assert project([source], as_of=AS_OF).plan.plan_digest
