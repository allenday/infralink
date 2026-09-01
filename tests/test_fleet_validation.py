"""Read-only fleet validation contracts."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import ValidationError

from infralink.cli.main import cli
from infralink.fleet.live_evidence import FleetPrometheusTargets
from infralink.fleet.prometheus_evidence import FleetPrometheusEvidence
from infralink.fleet.validation import validate_fleet
from infralink.mcp_server import _native_paths, _native_tool
from infralink.operator_config import FleetPrometheusEvidenceConfig
from infralink.operator_sources import SourceRequest, load_sources
from infralink.operator_surface import operator_surface

HOST_ID = "11111111-1111-4111-8111-111111111111"
EDGE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
REGISTRY_REVISION = "0123456789abcdef0123456789abcdef01234567"
TARGET_ID = "prometheus-service-app-metrics-up"


def _write_registry(root: Path, *, role_overrides: str = "", service: str = "app") -> Path:
    manifest = root / "hosts" / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "\n".join(
            [
                "hosts:",
                f"  {HOST_ID}:",
                "    canonical_name: app-1",
                "    status: active",
                "    roles: [app]",
                *(
                    [
                        "    role_overrides:",
                        "      app:",
                        *[f"        {line}" for line in role_overrides.splitlines()],
                    ]
                    if role_overrides
                    else []
                ),
                "    services:",
                f"      {service}: {{}}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    catalog = root / "ansible" / "services.yml"
    catalog.parent.mkdir(parents=True)
    catalog.write_text(
        "\n".join(
            [
                "roles:",
                "  app:",
                "    requires_params: [workers]",
                "    requires_roles: []",
                "    compose_service: app",
                "services:",
                "  app:",
                "    compose_service: app",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (root / "hosts" / HOST_ID / "docker-compose.yml.j2").write_text(
        "services:\n  app: {}\n", encoding="utf-8"
    )
    edges = root / "network" / "main-dev" / "edges" / "edges.yml"
    edges.parent.mkdir(parents=True)
    edges.write_text("edges: []\n", encoding="utf-8")
    return edges


def _write_compose(root: Path, content: str) -> None:
    (root / "hosts" / HOST_ID / "docker-compose.yml.j2").write_text(content, encoding="utf-8")


def test_static_validation_reports_missing_role_parameter(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path)

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert result.valid is False
    assert result.mode == "static"
    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("role_parameter_missing", "error")
    ]
    assert result.diagnostics[0].subject_id == "app-1"


def test_static_validation_reports_missing_compose_template(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    (tmp_path / "hosts" / HOST_ID / "docker-compose.yml.j2").unlink()

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("compose_template_missing", "error")
    ]


def test_static_validation_checks_literal_compose_services(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    _write_compose(tmp_path, "services:\n  other: {}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("role_compose_service_missing", "error"),
        ("service_compose_service_missing", "warning"),
    ]


def test_static_validation_extracts_services_from_jinja_compose_template(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    _write_compose(
        tmp_path,
        "services:\n  app:\n    image: {{ app_image | default('example/app:latest') }}\n",
    )

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert result.valid is True


def test_static_validation_extracts_services_from_root_jinja_include(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    template = tmp_path / "hosts" / "_templates" / "watchtower-services.yml.j2"
    template.parent.mkdir(exist_ok=True)
    template.write_text("services:\n  app:\n    image: {{ app_image }}\n", encoding="utf-8")
    _write_compose(
        tmp_path, "{% include 'watchtower-services.yml.j2' %}\nnetworks:\n  default: {}\n"
    )

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert result.valid is True


def test_static_validation_recursively_expands_safe_literal_includes(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    templates = tmp_path / "hosts" / "_templates"
    templates.mkdir(exist_ok=True)
    (templates / "first.yml.j2").write_text("{% include 'second.yml.j2' %}\n", encoding="utf-8")
    (templates / "second.yml.j2").write_text("services:\n  app: {}\n", encoding="utf-8")
    _write_compose(tmp_path, "{% include 'first.yml.j2' %}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert result.valid is True


def test_static_validation_rejects_literal_include_outside_registry(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    outside = tmp_path.parent / "outside.yml.j2"
    outside.write_text("services:\n  app: {}\n", encoding="utf-8")
    _write_compose(tmp_path, "{% include '../../outside.yml.j2' %}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("compose_template_include_unsafe", "capability_gap")
    ]


def test_static_validation_rejects_whitespace_control_include_outside_registry(
    tmp_path: Path,
) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    outside = tmp_path.parent / "outside.yml.j2"
    outside.write_text("services:\n  app: {}\n", encoding="utf-8")
    _write_compose(tmp_path, "{%- include '../../outside.yml.j2' -%}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("compose_template_include_unsafe", "capability_gap")
    ]


def test_static_validation_reports_unresolved_include_as_capability_gap(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    _write_compose(tmp_path, "{% include 'not-present.yml.j2' %}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("compose_template_include_unresolved", "capability_gap")
    ]


def test_static_validation_reports_dynamic_include_as_capability_gap(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    _write_compose(tmp_path, "{% include compose_fragment %}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("compose_template_include_unresolved", "capability_gap")
    ]


def test_static_validation_reports_literal_include_cycles_as_capability_gap(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    template = tmp_path / "hosts" / "_templates" / "cycle.yml.j2"
    template.parent.mkdir(exist_ok=True)
    template.write_text("{% include 'cycle.yml.j2' %}\n", encoding="utf-8")
    _write_compose(tmp_path, "{% include 'cycle.yml.j2' %}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("compose_template_include_cycle", "capability_gap")
    ]


def test_static_validation_reports_include_depth_as_capability_gap(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    templates = tmp_path / "hosts" / "_templates"
    templates.mkdir(exist_ok=True)
    for index in range(17):
        name = f"depth-{index}.yml.j2"
        content = (
            "services:\n  app: {}\n"
            if index == 16
            else f"{{% include 'depth-{index + 1}.yml.j2' %}}\n"
        )
        (templates / name).write_text(content, encoding="utf-8")
    _write_compose(tmp_path, "{% include 'depth-0.yml.j2' %}\n")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("compose_template_include_depth_exceeded", "capability_gap")
    ]


def _live_config(tmp_path: Path, evidence_path: Path) -> Path:
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    other_private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33)))
    other_public_key = other_private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    config = tmp_path / "operator.yml"
    config.write_text(
        "\n".join(
            [
                f"registry: {tmp_path}",
                "fleet_prometheus_evidence:",
                f"  artifact_path: {evidence_path}",
                "  trusted_public_keys:",
                f"    fleet-evidence-v1: {base64.b64encode(public_key).decode('ascii')}",
                f"    other-evidence-v1: {base64.b64encode(other_public_key).decode('ascii')}",
                "  signing_binding_key_ids:",
                "    infralink-ops/fleet-prometheus-evidence-signing:",
                "      - fleet-evidence-v1",
                "    infralink-ops/other-signing-binding:",
                "      - other-evidence-v1",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return config


def _write_live_declaration(root: Path) -> None:
    path = root / "operations" / "observation" / "fleet-prometheus-targets.yml"
    path.parent.mkdir(parents=True)
    path.write_text(
        "\n".join(
            [
                "schema_version: infralink.fleet-prometheus-targets/v1",
                "controller_bindings:",
                "  prometheus_credential_binding_ref: infralink-ops/fleet-prometheus-readonly",
                "  signing_binding_ref: infralink-ops/fleet-prometheus-evidence-signing",
                "targets:",
                f"  - id: {TARGET_ID}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _write_registry_revision(root: Path) -> None:
    git_dir = root / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(f"{REGISTRY_REVISION}\n", encoding="ascii")


def _write_live_evidence(
    path: Path,
    *,
    key_id: str = "fleet-evidence-v1",
    private_key: Ed25519PrivateKey | None = None,
    **overrides: object,
) -> None:
    private_key = private_key or Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    payload: dict[str, object] = {
        "schema_version": "infralink.fleet-prometheus-evidence/v1",
        "registry_revision": REGISTRY_REVISION,
        "generated_at": "2026-09-01T12:00:00Z",
        "window_seconds": 600,
        "max_age_seconds": 900,
        "targets": {
            TARGET_ID: {
                "status": "observed",
                "observed_at": "2026-09-01T11:59:55Z",
                "detail_code": "sample_observed",
            }
        },
        "signature": {
            "key_id": key_id,
            "algorithm": "ed25519",
            "value": base64.b64encode(bytes(64)).decode("ascii"),
        },
    }
    payload.update(overrides)
    evidence = FleetPrometheusEvidence.model_validate(payload)
    signature = base64.b64encode(private_key.sign(evidence.canonical_signed_bytes())).decode(
        "ascii"
    )
    path.write_text(
        evidence.model_copy(
            update={"signature": evidence.signature.model_copy(update={"value": signature})}
        ).model_dump_json(indent=2),
        encoding="utf-8",
    )


def _live_result(tmp_path: Path, monkeypatch, evidence_path: Path):
    edges = tmp_path / "network" / "main-dev" / "edges" / "edges.yml"
    if not edges.exists():
        edges = _write_registry(tmp_path, role_overrides="workers: 1")
        _write_registry_revision(tmp_path)
        _write_live_declaration(tmp_path)
    monkeypatch.setenv("INFRALINK_CONFIG", str(_live_config(tmp_path, evidence_path)))
    return validate_fleet(
        load_sources(SourceRequest(registry=tmp_path, edges=edges)),
        live=True,
        now=datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc),
    )


def test_live_evidence_contract_is_strict_and_registry_only() -> None:
    with pytest.raises(ValidationError):
        FleetPrometheusTargets.model_validate(
            {"schema_version": "infralink.fleet-prometheus-targets/v1", "targets": []}
        )


def test_live_evidence_operator_config_is_strict_and_local() -> None:
    trusted_key = base64.b64encode(bytes(range(32))).decode("ascii")
    with pytest.raises(ValidationError):
        FleetPrometheusEvidenceConfig.model_validate(
            {
                "artifact_path": "evidence.json",
                "trusted_public_keys": {"fleet-evidence-v1": trusted_key},
                "signing_binding_key_ids": {
                    "infralink-ops/fleet-prometheus-evidence-signing": ["fleet-evidence-v1"]
                },
            }
        )
    with pytest.raises(ValidationError):
        FleetPrometheusEvidenceConfig.model_validate(
            {
                "artifact_path": "/var/lib/infralink/evidence.json",
                "trusted_public_keys": {"fleet-evidence-v1": "not-a-public-key"},
                "signing_binding_key_ids": {
                    "infralink-ops/fleet-prometheus-evidence-signing": ["fleet-evidence-v1"]
                },
                "unexpected": "rejected",
            }
        )


def test_live_validation_has_no_public_provider_or_artifact_inputs() -> None:
    native = _native_tool("infralink_fleet_validate", ("fleet", "validate"))
    properties = native.input_schema["properties"]

    for forbidden in (
        "artifact_path",
        "evidence_path",
        "prometheus_url",
        "query",
        "token",
        "credential",
        "trusted_public_keys",
    ):
        assert forbidden not in properties

    source = Path(FleetPrometheusTargets.__module__.replace(".", "/") + ".py")
    implementation = (Path(__file__).parents[1] / "src" / source).read_text(encoding="utf-8")
    for forbidden in ("subprocess", "urllib", "socket", "requests", "httpx"):
        assert forbidden not in implementation


def test_live_cli_rejects_artifact_and_provider_options() -> None:
    result = CliRunner().invoke(
        cli,
        ["fleet", "validate", "--live", "--artifact-path", "/tmp/evidence.json"],
    )

    assert result.exit_code == 2


def test_static_mode_does_not_read_configured_live_evidence(tmp_path: Path, monkeypatch) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    monkeypatch.setenv("INFRALINK_CONFIG", str(_live_config(tmp_path, tmp_path / "absent.json")))

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert result.valid is True
    assert result.mode == "static"
    assert result.live_evidence is None


def test_live_mode_reports_unavailable_configured_evidence(tmp_path: Path, monkeypatch) -> None:
    result = _live_result(tmp_path, monkeypatch, tmp_path / "absent.json")

    assert result.valid is False
    assert result.mode == "live"
    assert result.live_evidence is not None
    assert result.live_evidence.status == "unavailable"
    assert {item.code for item in result.diagnostics} == {"live_evidence_unavailable"}


def test_live_mode_rejects_an_unbounded_configured_artifact(tmp_path: Path, monkeypatch) -> None:
    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_bytes(b"{" + b" " * 1_048_576)

    result = _live_result(tmp_path, monkeypatch, evidence_path)

    assert result.valid is False
    assert {item.code for item in result.diagnostics} == {"live_evidence_unavailable"}


def test_live_mode_accepts_fresh_signed_complete_evidence(tmp_path: Path, monkeypatch) -> None:
    evidence_path = tmp_path / "evidence.json"
    _write_live_evidence(evidence_path)
    result = _live_result(tmp_path, monkeypatch, evidence_path)

    assert result.valid is True
    assert result.live_evidence is not None
    assert result.live_evidence.status == "fresh"
    assert result.live_evidence.generated_at == "2026-09-01T12:00:00Z"


def test_live_mode_rejects_key_trusted_for_a_different_signing_binding(
    tmp_path: Path, monkeypatch
) -> None:
    evidence_path = tmp_path / "evidence.json"
    _write_live_evidence(
        evidence_path,
        key_id="other-evidence-v1",
        private_key=Ed25519PrivateKey.from_private_bytes(bytes(range(1, 33))),
    )

    result = _live_result(tmp_path, monkeypatch, evidence_path)

    assert result.valid is False
    assert {item.code for item in result.diagnostics} == {"live_evidence_key_unauthorized"}


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        (
            {
                "generated_at": "2026-09-01T10:00:00Z",
                "targets": {
                    TARGET_ID: {
                        "status": "observed",
                        "observed_at": "2026-09-01T09:59:55Z",
                        "detail_code": "sample_observed",
                    }
                },
            },
            "live_evidence_stale",
        ),
        ({"registry_revision": "f" * 40}, "live_evidence_revision_mismatch"),
    ],
)
def test_live_mode_rejects_stale_or_wrong_revision_evidence(
    tmp_path: Path, monkeypatch, overrides: dict[str, object], code: str
) -> None:
    evidence_path = tmp_path / "evidence.json"
    _write_live_evidence(evidence_path, **overrides)
    result = _live_result(tmp_path, monkeypatch, evidence_path)

    assert result.valid is False
    assert code in {item.code for item in result.diagnostics}


def test_live_mode_rejects_invalid_signature_incomplete_coverage_and_provider_failure(
    tmp_path: Path, monkeypatch
) -> None:
    evidence_path = tmp_path / "evidence.json"
    _write_live_evidence(evidence_path)
    payload = json.loads(evidence_path.read_text(encoding="utf-8"))
    payload["signature"]["value"] = base64.b64encode(bytes(64)).decode("ascii")
    evidence_path.write_text(json.dumps(payload), encoding="utf-8")
    invalid_signature = _live_result(tmp_path, monkeypatch, evidence_path)
    assert "live_evidence_signature_invalid" in {
        item.code for item in invalid_signature.diagnostics
    }

    _write_live_evidence(
        evidence_path,
        targets={
            "other-target": {
                "status": "observed",
                "observed_at": "2026-09-01T11:59:55Z",
                "detail_code": "sample_observed",
            }
        },
    )
    incomplete = _live_result(tmp_path, monkeypatch, evidence_path)
    assert "live_evidence_coverage_incomplete" in {item.code for item in incomplete.diagnostics}

    _write_live_evidence(
        evidence_path,
        targets={
            TARGET_ID: {
                "status": "query_error",
                "observed_at": None,
                "detail_code": "query_timeout",
            }
        },
    )
    provider_failure = _live_result(tmp_path, monkeypatch, evidence_path)
    assert "live_evidence_provider_failure" in {item.code for item in provider_failure.diagnostics}


def test_static_validation_reports_unknown_role_and_is_deterministic(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")
    manifest = tmp_path / "hosts" / HOST_ID / "manifest.yml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace("roles: [app]", "roles: [missing]"),
        encoding="utf-8",
    )

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)))

    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("unknown_role", "error")
    ]


def test_fleet_validation_is_a_read_only_operator_operation() -> None:
    assert operator_surface.operations.describe("fleet.validate").read_only is True
    assert _native_paths()["infralink_fleet_validate"] == ("fleet", "validate")


def test_validator_has_no_host_operation_dependencies() -> None:
    source = Path(validate_fleet.__code__.co_filename).read_text(encoding="utf-8")

    for forbidden in ("subprocess", "urllib", "socket", "bws", "paramiko"):
        assert forbidden not in source
    assert '"docker"' not in source
    assert "'docker'" not in source


def test_cli_returns_completed_negative_validation_result(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path)

    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(tmp_path),
            "--edges",
            str(edges),
            "--output",
            "json",
            "fleet",
            "validate",
        ],
    )

    payload = json.loads(result.output)
    assert result.exit_code == 1
    assert payload["ok"] is True
    assert payload["command"]["parsed"]["path"] == ["fleet", "validate"]
    assert payload["result"]["valid"] is False
    assert payload["next_actions"][0]["rel"] == "inspect-declaration"


def test_cli_replay_action_preserves_nondefault_edges(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path)
    result = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(tmp_path),
            "--edges",
            str(edges),
            "fleet",
            "validate",
        ],
    )

    payload = json.loads(result.output)

    assert "--edges" in payload["next_actions"][0]["command"]
    assert str(edges) in payload["next_actions"][0]["command"]
