from __future__ import annotations

import json
from pathlib import Path

import click
import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as JsonSchemaValidationError

from infralink.cli.main import cli

SOURCE = """\
schema_version: infralink.observation/v1
service_profiles:
  - id: web
    endpoints:
      - {id: http, protocol: http, port: 8080}
    health:
      - {id: ready, endpoint_id: http, evaluator: http-status}
    signals:
      - {id: up, capability_id: ready, evaluator: capability-state}
hosts:
  - {id: 11111111-1111-4111-8111-111111111111}
service_instances:
  - id: frontend
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: web
"""

V2_SOURCE = """\
schema_version: infralink.observation/v2
service_profiles:
  - id: smtp-stack
    components:
      - id: submission
        endpoints:
          - {id: smtp, protocol: smtp, port: 587}
      - id: delivery
        endpoints:
          - {id: smtp, protocol: smtp, port: 2525}
service_instances:
  - id: mta
    host_id: 11111111-1111-4111-8111-111111111111
    profile_id: smtp-stack
    components:
      - {slot_id: submission}
      - {slot_id: delivery}
component_edges:
  - id: submission-to-delivery
    source_endpoint_id: 11111111-1111-4111-8111-111111111111/mta/submission/smtp
    target_endpoint_id: 11111111-1111-4111-8111-111111111111/mta/delivery/smtp
"""


def _source(tmp_path: Path, text: str = SOURCE) -> Path:
    path = tmp_path / "observation.yml"
    path.write_text(text, encoding="utf-8")
    return path


def test_observation_validate_defaults_to_yaml_and_json_is_equivalent(tmp_path: Path) -> None:
    source = _source(tmp_path)
    args = ["validate", "--source", str(source), "--as-of", "2026-08-04T00:00:00Z"]

    yaml_result = CliRunner().invoke(cli, args)
    json_result = CliRunner().invoke(cli, ["--output", "json", *args])

    assert yaml_result.exit_code == json_result.exit_code == 0
    assert yaml_result.stderr == json_result.stderr == ""
    yaml_payload = yaml.safe_load(yaml_result.output)
    json_payload = json.loads(json_result.output)
    for payload in (yaml_payload, json_payload):
        assert payload["schema_version"] == "agent-cli.response.v1"
        assert payload["ok"] is True
        assert payload["result"]["valid"] is True
        assert payload["request_id"]
        assert payload["generated_at"].endswith("Z")
        assert "error" not in payload
    assert yaml_payload["result"] == json_payload["result"]


def test_project_observation_and_capabilities_are_offline_yaml(tmp_path: Path) -> None:
    source = _source(tmp_path)
    runner = CliRunner()
    projected = runner.invoke(
        cli,
        [
            "project",
            "observation",
            "--source",
            str(source),
            "--as-of",
            "2026-08-04T00:00:00Z",
        ],
    )
    capabilities = runner.invoke(cli, ["capabilities"])

    assert projected.exit_code == capabilities.exit_code == 0
    payload = yaml.safe_load(projected.output)
    assert payload["result"]["plan"]["schema_version"] == "infralink.plan.v1"
    assert any(action["rel"] == "validate" for action in payload["next_actions"])
    advertised = yaml.safe_load(capabilities.output)["result"]
    assert "infralink.observation/v1" in advertised["document_schema_versions"]
    assert "infralink.observation/v2" in advertised["document_schema_versions"]
    assert "observation" in advertised["projections"]
    assert "redis-ready" in advertised["evaluator_types"]["health"]


def test_observation_validate_accepts_declared_v2_source(tmp_path: Path) -> None:
    source = _source(tmp_path, V2_SOURCE)

    result = CliRunner().invoke(
        cli,
        ["validate", "--source", str(source), "--as-of", "2026-08-04T00:00:00Z"],
    )

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["result"]["valid"] is True
    assert payload["result"]["diagnostics"]["error_count"] == 0


def test_observation_validate_reports_v2_edge_location(tmp_path: Path) -> None:
    source = _source(
        tmp_path, V2_SOURCE.replace("protocol: smtp, port: 2525", "protocol: tcp, port: 2525")
    )

    result = CliRunner().invoke(
        cli,
        ["validate", "--source", str(source), "--as-of", "2026-08-04T00:00:00Z"],
    )

    assert result.exit_code == 1
    payload = yaml.safe_load(result.output)
    assert payload["error"]["code"] == "component-edge-incompatible-protocol"
    diagnostic = payload["error"]["details"]["diagnostics"][0]
    assert diagnostic["location"] == {
        "path": source.name,
        "pointer": "/component_edges/0",
        "document_index": 0,
    }


def test_observation_validate_rejects_missing_v2_component_slot(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        V2_SOURCE.replace("      - {slot_id: delivery}\n", ""),
    )

    result = CliRunner().invoke(
        cli,
        ["validate", "--source", str(source), "--as-of", "2026-08-04T00:00:00Z"],
    )

    assert result.exit_code == 1
    payload = yaml.safe_load(result.output)
    assert payload["error"]["code"] == "service-instance-missing-component-slot"
    diagnostic = payload["error"]["details"]["diagnostics"][0]
    assert diagnostic["location"] == {
        "path": source.name,
        "pointer": "/service_instances/0/components",
        "document_index": 0,
    }


def test_observation_validate_rejects_mixed_source_versions(tmp_path: Path) -> None:
    _source(tmp_path, SOURCE)
    (tmp_path / "v2.yml").write_text(V2_SOURCE, encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["validate", "--source", str(tmp_path), "--as-of", "2026-08-04T00:00:00Z"],
    )

    assert result.exit_code == 1
    payload = yaml.safe_load(result.output)
    assert payload["error"]["code"] == "mixed-observation-schema-versions"
    diagnostic = payload["error"]["details"]["diagnostics"][0]
    assert diagnostic["location"]["pointer"] == "/schema_version"


def test_project_view_exposes_profile_metrics_membership(tmp_path: Path) -> None:
    source = _source(
        tmp_path,
        (
            SOURCE
            + """\
observation_backends:
  - {id: metrics-primary, kind: metrics, backend_ref: prometheus}
datasource_bindings:
  - {id: primary-metrics, observation_backend_id: metrics-primary, datasource_ref: main}
operations_views:
  - id: nginx
    purpose: Fleet NGINX metrics.
    kind: profile_metrics
    metric_profile_id: web
    datasource_binding_id: primary-metrics
    sections: []
"""
        ).replace(
            "    health:\n",
            "    metrics:\n      - {id: metrics, endpoint_id: http, evaluator: prometheus-scrape}\n    health:\n",
        ),
    )

    result = CliRunner().invoke(
        cli,
        [
            "project",
            "view",
            "nginx",
            "--source",
            str(source),
            "--as-of",
            "2026-08-04T00:00:00Z",
        ],
    )

    assert result.exit_code == 0
    payload = yaml.safe_load(result.output)
    assert payload["result"]["view"]["kind"] == "profile_metrics"
    assert payload["result"]["view"]["host_ids"] == ["11111111-1111-4111-8111-111111111111"]
    assert payload["result"]["view"]["service_ids"] == [
        "11111111-1111-4111-8111-111111111111/frontend"
    ]
    assert payload["result"]["view"]["source_refs"][0]["path"] == source.name
    assert any(action["rel"] == "validate" for action in payload["next_actions"])


def test_observation_errors_are_typed_and_exact_exit_codes(tmp_path: Path) -> None:
    unsupported = _source(tmp_path, "schema_version: infralink.observation/v99\n")
    invalid = CliRunner().invoke(
        cli,
        ["validate", "--source", str(unsupported), "--as-of", "2026-08-04T00:00:00Z"],
    )
    missing = CliRunner().invoke(cli, ["explain", "not-a-code"])

    assert invalid.exit_code == 2
    assert yaml.safe_load(invalid.output)["error"]["code"] == "schema-version-unsupported"
    assert missing.exit_code == 1
    assert yaml.safe_load(missing.output)["error"]["code"] == "diagnostic-code-not-found"
    assert invalid.stderr == missing.stderr == ""


def test_legacy_validate_defaults_to_yaml_after_new_command_invocation(tmp_path: Path) -> None:
    runner = CliRunner()
    runner.invoke(cli, ["capabilities"])
    result = runner.invoke(cli, ["--registry", "missing.yml", "validate"])

    assert yaml.safe_load(result.output)["schema_version"] == "infralink.cli/v1"


def test_generated_observation_schemas_validate_public_examples() -> None:
    root = Path(__file__).parents[1]
    example_root = root / "examples/observation"
    profiles = yaml.safe_load((example_root / "profiles.yml").read_text())
    instances = yaml.safe_load((example_root / "instances.yml").read_text())
    edges = yaml.safe_load((example_root / "edges.yml").read_text())
    secrets = yaml.safe_load((example_root / "secrets.yml").read_text())
    operations = yaml.safe_load((example_root / "operations.yml").read_text())
    documents = {
        "profile": profiles,
        "instance": instances,
        "application": {
            "schema_version": instances["schema_version"],
            "applications": instances["applications"],
        },
        "dependency": edges,
        "secrets": secrets,
        "operations-view": operations,
        "readiness-suite": {
            "schema_version": operations["schema_version"],
            "readiness_suites": operations["readiness_suites"],
        },
    }
    for schema_name, document in documents.items():
        schema = json.loads(
            (root / "src/infralink/schemas/observation/v1" / f"{schema_name}.json").read_text()
        )
        Draft202012Validator(schema).validate(document)
        missing = dict(document)
        missing.pop("schema_version")
        with pytest.raises(JsonSchemaValidationError):
            Draft202012Validator(schema).validate(missing)
        wrong = dict(document, schema_version="infralink.observation/v99")
        with pytest.raises(JsonSchemaValidationError):
            Draft202012Validator(schema).validate(wrong)


def test_generated_schemas_publish_redis_protocol_and_readiness() -> None:
    root = Path(__file__).parents[1] / "src/infralink/schemas"
    profile_schema = (root / "observation/v1/profile.json").read_text()
    dependency_schema = (root / "observation/v1/dependency.json").read_text()
    projection_schema = (root / "cli/v1/project-observation.json").read_text()

    for schema in (profile_schema, dependency_schema, projection_schema):
        assert '"redis"' in schema
    assert '"redis-ready"' in profile_schema


def test_end_to_end_example_directory_validates_and_projects() -> None:
    source = Path(__file__).parents[1] / "examples/observation"
    args = ["--source", str(source), "--as-of", "2026-08-04T00:00:00Z"]
    validated = CliRunner().invoke(cli, ["validate", *args])
    projected = CliRunner().invoke(cli, ["project", "observation", *args])

    assert validated.exit_code == projected.exit_code == 0
    plan = yaml.safe_load(projected.output)["result"]["plan"]
    assert plan["plan_digest"]
    signals = {item["id"]: item for item in plan["signals"]}
    nginx = "service/11111111-1111-4111-8111-111111111111/web"
    ci = "service/22222222-2222-4222-8222-222222222222/ci"
    assert signals[f"{nginx}/health/up"]["capability_path"] == "/healthz"
    assert signals[f"{nginx}/metrics/requests"]["capability_path"] == "/metrics"
    assert signals[f"{nginx}/access/traffic"]["log_stream"] == "nginx.access"
    assert signals[f"{ci}/builds/failures"]["log_stream"] == "ci.builds"
    view_signal = signals["view/service-overview/query/core/web-up"]
    assert view_signal["capability_path"] == "/healthz"
    assert signals["view/service-overview/query/core/web-traffic"]["log_stream"] == "nginx.access"
    assert signals["view/service-overview/query/core/ci-failures"]["log_stream"] == "ci.builds"


def test_observation_invocation_and_internal_failures_keep_agent_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = _source(tmp_path)
    invocation = CliRunner().invoke(cli, ["validate", "--source", str(source)])

    monkeypatch.setattr("infralink.cli.observation._request_id", lambda: 1 / 0)
    internal = CliRunner().invoke(cli, ["capabilities"])

    assert invocation.exit_code == 2
    assert yaml.safe_load(invocation.output)["schema_version"] == "agent-cli.response.v1"
    assert yaml.safe_load(invocation.output)["error"]["code"] == "invocation-error"
    assert internal.exit_code == 4
    assert yaml.safe_load(internal.output)["schema_version"] == "agent-cli.response.v1"
    assert yaml.safe_load(internal.output)["error"]["code"] == "internal-invariant"
    assert invocation.stderr == internal.stderr == ""


@pytest.mark.parametrize(
    ("output_args", "expected"),
    [
        (("-o", "json"), "json"),
        (("--output", "json"), "json"),
        (("--output=json",), "json"),
        (("-ojson",), "json"),
        (("-vojson",), "json"),
        (("-o", "yaml"), "yaml"),
        (("--output", "yaml"), "yaml"),
        (("--output=yaml",), "yaml"),
        (("-oyaml",), "yaml"),
        (("-voyaml",), "yaml"),
        (("--output", "yaml", "--output=json"), "json"),
        (("-ojson", "-o", "yaml"), "yaml"),
    ],
)
@pytest.mark.parametrize("failure", ["project", "validate"])
def test_observation_boundary_failure_honors_click_output_spellings(
    tmp_path: Path,
    output_args: tuple[str, ...],
    expected: str,
    failure: str,
) -> None:
    source = _source(tmp_path)
    command = ["project"] if failure == "project" else ["validate", "--source", str(source)]

    result = CliRunner().invoke(cli, [*output_args, *command])

    assert result.exit_code == 2
    assert result.stderr == ""
    if expected == "json":
        assert result.output.startswith("{")
        payload = json.loads(result.output)
    else:
        assert result.output.startswith("schema_version:")
        payload = yaml.safe_load(result.output)
    assert payload["schema_version"] == "agent-cli.response.v1"
    assert payload["error"]["code"] == "invocation-error"


def test_legacy_boundary_failure_defaults_to_yaml() -> None:
    result = CliRunner().invoke(cli, ["resolve"])

    assert result.exit_code == 2
    assert yaml.safe_load(result.output)["schema_version"] == "infralink.cli/v1"


@pytest.mark.parametrize(
    ("output_args", "expected"),
    [
        (("-o", "json"), "json"),
        (("--output=json",), "json"),
        (("-vojson",), "json"),
        (("--output", "yaml"), "yaml"),
        (("-oyaml",), "yaml"),
        (("--output=json", "-oyaml"), "yaml"),
    ],
)
def test_observation_internal_failure_honors_explicit_output(
    monkeypatch: pytest.MonkeyPatch, output_args: tuple[str, ...], expected: str
) -> None:
    monkeypatch.setattr("infralink.cli.observation._request_id", lambda: 1 / 0)

    result = CliRunner().invoke(cli, [*output_args, "capabilities"])

    assert result.exit_code == 4
    if expected == "json":
        assert json.loads(result.output)["error"]["code"] == "internal-invariant"
    else:
        assert result.output.startswith("schema_version:")
        assert yaml.safe_load(result.output)["error"]["code"] == "internal-invariant"


@pytest.mark.parametrize("output_args", [("--output=json",), ("-oyaml",)])
@pytest.mark.parametrize(
    "secret_args",
    [
        ("--token", "split-canary"),
        ("--password=equals-canary",),
        ("-pattached-canary",),
    ],
)
@pytest.mark.parametrize("code", ["invocation-error", "internal-invariant"])
def test_boundary_envelope_redacts_sensitive_argv_everywhere(
    output_args: tuple[str, ...], secret_args: tuple[str, ...], code: str
) -> None:
    from infralink.cli.observation import emit_boundary_failure

    runner = CliRunner()

    @click.command()
    def probe() -> None:
        emit_boundary_failure(
            [*output_args, "project", *secret_args], code=code, message="boundary failure"
        )

    result = runner.invoke(probe)
    assert result.exit_code == 0
    assert "canary" not in result.output
    payload = (
        json.loads(result.output)
        if output_args == ("--output=json",)
        else yaml.safe_load(result.output)
    )
    assert "[REDACTED]" in payload["command"]["raw_redacted"]
    assert "canary" not in json.dumps(payload)


@pytest.mark.parametrize("output_args", [("--output=json",), ("-oyaml",)])
def test_source_equals_selects_observation_boundary_envelope(
    tmp_path: Path, output_args: tuple[str, ...]
) -> None:
    source = _source(tmp_path)
    result = CliRunner().invoke(cli, [*output_args, "validate", f"--source={source}"])

    assert result.exit_code == 2
    payload = (
        json.loads(result.output)
        if output_args == ("--output=json",)
        else yaml.safe_load(result.output)
    )
    assert payload["schema_version"] == "agent-cli.response.v1"
    assert payload["error"]["code"] == "invocation-error"


@pytest.mark.parametrize("output_args", [("--output=json",), ("-oyaml",)])
def test_source_equals_internal_failure_keeps_observation_envelope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output_args: tuple[str, ...]
) -> None:
    source = _source(tmp_path)
    monkeypatch.setattr(
        "infralink.cli.observation._parse_as_of",
        lambda value: (_ for _ in ()).throw(RuntimeError("injected")),
    )
    result = CliRunner().invoke(
        cli,
        [*output_args, "validate", f"--source={source}", "--as-of", "2026-08-04T00:00:00Z"],
    )

    assert result.exit_code == 4
    payload = (
        json.loads(result.output)
        if output_args == ("--output=json",)
        else yaml.safe_load(result.output)
    )
    assert payload["schema_version"] == "agent-cli.response.v1"
    assert payload["error"]["code"] == "internal-invariant"


@pytest.mark.parametrize("output_args", [("--output=json",), ("-oyaml",)])
def test_validate_resolves_registry_revision_in_command_metadata(
    tmp_path: Path, output_args: tuple[str, ...]
) -> None:
    source = _source(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            *output_args,
            "validate",
            "--source",
            str(source),
            "--as-of",
            "2026-08-04T00:00:00Z",
            "--registry-revision",
            "revision-7",
        ],
    )
    payload = (
        json.loads(result.output)
        if output_args == ("--output=json",)
        else yaml.safe_load(result.output)
    )

    assert result.exit_code == 0
    assert payload["command"]["resolved"]["registry_revision"] == "revision-7"


def test_response_schemas_reject_wrong_and_extra_nested_domain_fields() -> None:
    root = Path(__file__).parents[1]
    source = root / "examples/observation"
    projected = CliRunner().invoke(
        cli,
        [
            "--output=json",
            "project",
            "observation",
            "--source",
            str(source),
            "--as-of",
            "2026-08-04T00:00:00Z",
        ],
    )
    validated = CliRunner().invoke(
        cli,
        ["--output=json", "validate", "--source", str(source), "--as-of", "2026-08-04T00:00:00Z"],
    )
    projection_payload = json.loads(projected.output)
    validation_payload = json.loads(validated.output)
    projection_schema = json.loads(
        (root / "src/infralink/schemas/cli/v1/project-observation.json").read_text()
    )
    validation_schema = json.loads(
        (root / "src/infralink/schemas/cli/v1/observation-validate.json").read_text()
    )

    Draft202012Validator(projection_schema).validate(projection_payload)
    Draft202012Validator(validation_schema).validate(validation_payload)
    projection_payload["result"]["plan"]["signals"][0]["unexpected"] = True
    validation_payload["result"]["diagnostics"]["error_count"] = "zero"
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(projection_schema).validate(projection_payload)
    with pytest.raises(JsonSchemaValidationError):
        Draft202012Validator(validation_schema).validate(validation_payload)
