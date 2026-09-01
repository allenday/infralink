"""Read-only fleet validation contracts."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from infralink.cli.main import cli
from infralink.fleet.validation import validate_fleet
from infralink.mcp_server import _native_paths
from infralink.operator_sources import SourceRequest, load_sources
from infralink.operator_surface import operator_surface

HOST_ID = "11111111-1111-4111-8111-111111111111"
EDGE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


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


def test_live_mode_returns_capability_gap_without_probing(tmp_path: Path) -> None:
    edges = _write_registry(tmp_path, role_overrides="workers: 1")

    result = validate_fleet(load_sources(SourceRequest(registry=tmp_path, edges=edges)), live=True)

    assert result.valid is False
    assert result.mode == "live"
    assert [(item.code, item.severity) for item in result.diagnostics] == [
        ("live_evidence_unavailable", "capability_gap")
    ]


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
