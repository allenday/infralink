from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
from jsonschema import Draft202012Validator

from infralink.cli.main import cli
from infralink.health.checks import HealthCheckResult

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
HOST_ID = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
EDGE_ID = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"


def _invoke(*args: str):
    return CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "doctor",
            *args,
        ],
    )


def _health(edge_id: str, *, healthy: bool = True) -> HealthCheckResult:
    return HealthCheckResult(
        edge_id=edge_id,
        edge_type="monitoring",
        target_endpoint="redacted.invalid:443",
        healthy=healthy,
        latency_ms=1.0 if healthy else None,
        message=None,
        criticality="high",
        check_type="tcp",
        timestamp=0,
        error_code=None if healthy else "timeout",
    )


def test_doctor_is_discoverable_and_global_doctor_is_read_only() -> None:
    help_result = CliRunner().invoke(cli, ["help"])
    help_payload = __import__("yaml").safe_load(help_result.output)
    assert "doctor" in {child["name"] for child in help_payload["result"]["children"]}

    result = _invoke()
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["result"] == {
        "target": {"type": "global", "id": None, "canonical_name": None},
        "declared": {"host_count": 5, "service_count": 13, "edge_count": 5},
        "checks": [],
        "status": "unknown",
        "reason": "no_observation_evidence",
    }


def test_doctor_host_accepts_canonical_name_and_aggregates_live_edge_evidence(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "infralink.cli.doctor.check_edge_health",
        lambda edge, resolver, timeout: _health(edge.id),
    )

    result = _invoke("host", "database.example.com")
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["target"] == {
        "type": "host",
        "id": HOST_ID,
        "canonical_name": "database.example.com",
    }
    assert payload["result"]["declared"]["services"] == ["postgres-exporter", "redis-exporter"]
    assert payload["result"]["status"] == "healthy"
    assert payload["result"]["reason"] is None
    assert [check["edge_id"] for check in payload["result"]["checks"]] == [
        "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
        "0f3088c3-6b9b-4137-9b49-20448cd259de",
        "1d12ce0f-6382-459d-b3d5-a5550d3bc711",
        "7cfa416b-927f-4ae1-b59e-1f2df1d7220b",
        "c26fb664-a60d-41b4-be63-9fa520d698bf",
    ]
    assert payload["result"]["checks"][0] == {
        "edge_id": EDGE_ID,
        "status": "healthy",
        "latency_ms": 1.0,
        "error_code": None,
    }
    assert "redacted.invalid" not in result.output
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/doctor.json").read_text())
    Draft202012Validator(schema).validate(payload)


def test_doctor_edge_reports_negative_live_evidence_without_mutating_topology(monkeypatch) -> None:
    monkeypatch.setattr(
        "infralink.cli.doctor.check_edge_health",
        lambda edge, resolver, timeout: _health(edge.id, healthy=False),
    )

    result = _invoke("edge", EDGE_ID)
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert payload["ok"] is True
    assert payload["result"]["target"] == {
        "type": "edge",
        "id": EDGE_ID,
        "canonical_name": None,
    }
    assert payload["result"]["status"] == "unavailable"
    assert payload["result"]["checks"][0]["error_code"] == "timeout"
    assert {action["rel"] for action in payload["next_actions"]} >= {"show", "check"}


def test_doctor_service_and_profile_return_unknown_when_no_edge_evidence_exists() -> None:
    for target_type in ("service", "profile"):
        result = _invoke(target_type, "mariadb")
        payload = json.loads(result.output)
        assert result.exit_code == 0
        assert payload["result"]["target"] == {
            "type": target_type,
            "id": "mariadb",
            "canonical_name": None,
        }
        assert payload["result"]["status"] == "unknown"
        assert payload["result"]["reason"] == "no_observation_evidence"


def test_doctor_unknown_host_returns_a_bounded_canonical_discovery_action() -> None:
    result = _invoke("host", "missing-host")
    payload = json.loads(result.output)

    assert result.exit_code == 3
    assert payload["error"]["code"] == "entity_not_found"
    assert payload["next_actions"] == [
        {
            "rel": "list",
            "argv": ["infralink", "host", "list"],
            "command": "infralink host list",
            "description": "List host records",
            "safe": True,
            "templated": False,
            "bindings": {},
        }
    ]
