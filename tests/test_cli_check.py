import json
import shlex
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from infralink.cli.main import cli
from infralink.health.checks import HealthCheckResult

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def _health(
    edge_id: str,
    *,
    healthy: bool,
    message: str | None = None,
    criticality: str = "medium",
    check_type: str = "tcp",
    latency_ms: float | None = None,
    error_code: str | None = None,
) -> HealthCheckResult:
    return HealthCheckResult(
        edge_id=edge_id,
        edge_type="database",
        target_endpoint="secret.internal:5432",
        healthy=healthy,
        latency_ms=latency_ms,
        message=message,
        criticality=criticality,
        check_type=check_type,
        timestamp=123456.0,
        error_code=error_code,
    )


def _invoke(*args: str, edges_path: Path | None = None):
    return CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(edges_path or EXAMPLES / "edges.yml"),
            "check",
            *args,
        ],
    )


def _schema_validate(payload: dict) -> None:
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/check.json").read_text())
    Draft202012Validator(schema).validate(payload)


@pytest.mark.parametrize(
    (
        "healthy",
        "message",
        "check_type",
        "criticality",
        "status",
        "error_code",
        "exit_code",
    ),
    [
        (True, None, "tcp", "medium", "healthy", None, 0),
        (
            False,
            "Connection refused (code: 111)",
            "tcp",
            "critical",
            "unavailable",
            "connection_refused",
            1,
        ),
        (False, "Connection timed out", "tcp", "high", "unavailable", "timeout", 1),
        (
            False,
            "host provider canary",
            "resolution",
            "critical",
            "unavailable",
            "resolution_failed",
            1,
        ),
        (False, "provider canary", "tcp", "medium", "unhealthy", "check_failed", 1),
    ],
)
def test_check_returns_typed_completed_result_without_sensitive_fields(
    monkeypatch: pytest.MonkeyPatch,
    healthy: bool,
    message: str | None,
    check_type: str,
    criticality: str,
    status: str,
    error_code: str | None,
    exit_code: int,
) -> None:
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: _health(
            edge.id,
            healthy=healthy,
            message=message,
            criticality=criticality,
            check_type=check_type,
            latency_ms=1.25 if healthy else None,
            error_code=error_code,
        ),
    )

    result = _invoke("--edge", "058e29ff-57b9-47c8-b6fa-0914ac03e25c")
    payload = json.loads(result.output)

    assert result.exit_code == exit_code
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert payload["ok"] is True
    assert "error" not in payload
    assert payload["result"]["healthy"] is healthy
    assert payload["result"]["checks"]["items"] == [
        {
            "edge_id": "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
            "healthy": healthy,
            "status": status,
            "latency_ms": 1.25 if healthy else None,
            "error_code": error_code,
        }
    ]
    assert "secret.internal" not in result.output
    assert "provider canary" not in result.output
    assert "timestamp" not in result.output
    assert "criticality" not in result.output
    _schema_validate(payload)


def test_check_empty_filters_are_healthy_typed_result() -> None:
    result = _invoke("--edge", "missing")
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"] == {
        "healthy": True,
        "checks": {
            "items": [],
            "page": {
                "limit": 20,
                "returned": 0,
                "total": 0,
                "next_cursor": None,
            },
        },
        "summary": {"total": 0, "healthy": 0, "unhealthy": 0},
    }
    _schema_validate(payload)


def test_failed_check_advertises_edge_inspection_and_resolution_repairs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge_id = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: _health(
            edge.id,
            healthy=False,
            error_code="timeout",
        ),
    )

    result = _invoke("--edge", edge_id)
    payload = json.loads(result.output)
    actions = {item["rel"]: item["command"] for item in payload["next_actions"]}

    assert result.exit_code == 1
    assert actions["show"].endswith(f"edge show {edge_id}")
    assert actions["resolve"].endswith(f"resolve {edge_id}")


def test_failed_check_repair_actions_survive_a_healthy_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edge_ids = sorted(
        item["id"] for item in yaml.safe_load((EXAMPLES / "edges.yml").read_text())["edges"]
    )
    failed_id = edge_ids[1]
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: _health(
            edge.id,
            healthy=edge.id != failed_id,
            error_code=None if edge.id != failed_id else "timeout",
        ),
    )

    result = _invoke("--limit", "1")
    payload = json.loads(result.output)
    actions = {item["rel"]: item["command"] for item in payload["next_actions"]}

    assert result.exit_code == 1
    assert payload["result"]["checks"]["items"][0]["healthy"] is True
    assert actions["show"].endswith(f"edge show {failed_id}")
    assert actions["resolve"].endswith(f"resolve {failed_id}")


def test_check_filters_and_repeated_edges_are_preserved_in_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edges_path = tmp_path / "edges.yml"
    edges = yaml.safe_load((EXAMPLES / "edges.yml").read_text(encoding="utf-8"))
    edges["edges"][0]["metadata"]["criticality"] = "medium"
    edges_path.write_text(yaml.safe_dump(edges))
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: _health(edge.id, healthy=True),
    )
    first = json.loads(
        _invoke(
            "--edge",
            "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
            "--edge",
            "7cfa416b-927f-4ae1-b59e-1f2df1d7220b",
            "--type",
            "database",
            "--criticality",
            "medium",
            "--timeout",
            "9",
            "--limit",
            "1",
            edges_path=edges_path,
        ).output
    )
    action = next(item for item in first["next_actions"] if item["rel"] == "continue")

    monkeypatch.setenv("INFRALINK_REGISTRY", str(EXAMPLES / "registry.yml"))
    monkeypatch.setenv("INFRALINK_EDGES", str(edges_path))

    assert action["command"].endswith(
        "check --edge 058e29ff-57b9-47c8-b6fa-0914ac03e25c "
        "--edge 7cfa416b-927f-4ae1-b59e-1f2df1d7220b --type database "
        "--criticality medium --timeout 9 --collection checks --cursor '{cursor}' --limit 1"
    )
    cursor = first["result"]["checks"]["page"]["next_cursor"]
    replay = [cursor if item == "{cursor}" else item for item in shlex.split(action["command"])]
    second_result = CliRunner().invoke(cli, ["--output", "json", *replay[1:]])
    second = json.loads(second_result.output)
    assert second_result.exit_code == 0
    assert second["result"]["checks"]["items"] != first["result"]["checks"]["items"]
    assert second["result"]["summary"] == {"total": 2, "healthy": 2, "unhealthy": 0}


def test_check_critical_only_is_preserved_in_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: _health(edge.id, healthy=True),
    )
    first = json.loads(_invoke("--critical-only", "--limit", "1").output)
    action = next(item for item in first["next_actions"] if item["rel"] == "continue")

    assert "--critical-only" in shlex.split(action["command"])
    assert first["result"]["summary"] == {"total": 2, "healthy": 2, "unhealthy": 0}


def test_check_cursor_ignores_recomputed_health_observations_but_binds_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observations = {"invocation": 0}

    def changing_health(edge, resolver, timeout):
        observations["invocation"] += 1
        healthy = observations["invocation"] <= 2
        return _health(
            edge.id,
            healthy=healthy,
            message=None if healthy else "provider canary",
            latency_ms=float(observations["invocation"]),
        )

    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        changing_health,
    )
    first = json.loads(_invoke("--type", "database", "--timeout", "9", "--limit", "1").output)
    cursor = first["result"]["checks"]["page"]["next_cursor"]
    first_id = first["result"]["checks"]["items"][0]["edge_id"]

    payload, signature = cursor.split(".")
    tampered = f"{payload[:-1]}A.{signature}"
    tampered_result = _invoke(
        "--type",
        "database",
        "--timeout",
        "9",
        "--limit",
        "1",
        "--cursor",
        tampered,
    )
    assert tampered_result.exit_code == 2
    assert json.loads(tampered_result.output)["error"]["code"] == "invalid_cursor"

    mismatched = _invoke(
        "--type",
        "database",
        "--timeout",
        "10",
        "--limit",
        "1",
        "--collection",
        "checks",
        "--cursor",
        cursor,
    )
    assert mismatched.exit_code == 2
    assert json.loads(mismatched.output)["error"]["code"] == "invalid_cursor"

    replay = _invoke(
        "--type",
        "database",
        "--timeout",
        "9",
        "--limit",
        "1",
        "--collection",
        "checks",
        "--cursor",
        cursor,
    )
    replay_payload = json.loads(replay.output)
    assert replay.exit_code == 1
    assert replay_payload["ok"] is True
    assert replay_payload["result"]["checks"]["page"] == {
        "limit": 1,
        "returned": 1,
        "total": 2,
        "next_cursor": None,
    }
    assert replay_payload["result"]["checks"]["items"][0]["edge_id"] != first_id
    assert (
        replay_payload["result"]["checks"]["items"][0]["latency_ms"]
        > first["result"]["checks"]["items"][0]["latency_ms"]
    )


@pytest.mark.parametrize(
    "changed_requested_ids",
    [
        (
            "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
            "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
            "7cfa416b-927f-4ae1-b59e-1f2df1d7220b",
            "unmatched-b",
        ),
        (
            "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
            "7cfa416b-927f-4ae1-b59e-1f2df1d7220b",
            "unmatched-a",
        ),
    ],
)
def test_check_cursor_binds_canonical_requested_ids_including_unmatched_and_duplicates(
    monkeypatch: pytest.MonkeyPatch,
    changed_requested_ids: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: _health(edge.id, healthy=True),
    )
    requested_ids = (
        "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
        "058e29ff-57b9-47c8-b6fa-0914ac03e25c",
        "7cfa416b-927f-4ae1-b59e-1f2df1d7220b",
        "unmatched-a",
    )
    first_args = [item for edge_id in requested_ids for item in ("--edge", edge_id)]
    first = json.loads(_invoke(*first_args, "--limit", "1").output)
    cursor = first["result"]["checks"]["page"]["next_cursor"]
    changed_args = [item for edge_id in changed_requested_ids for item in ("--edge", edge_id)]

    changed = _invoke(
        *changed_args,
        "--limit",
        "1",
        "--collection",
        "checks",
        "--cursor",
        cursor,
    )

    assert changed.exit_code == 2
    assert json.loads(changed.output)["error"]["code"] == "invalid_cursor"


def test_check_expected_load_failure_and_unexpected_failure_use_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = CliRunner().invoke(cli, ["--output", "json", "--registry", "missing.yml", "check"])
    missing_payload = json.loads(missing.output)
    assert missing.exit_code == 3
    assert missing_payload["error"]["code"] == "input_load_failed"

    malformed_path = tmp_path / "malformed.yml"
    malformed_path.write_text("edges: [")
    malformed = _invoke(edges_path=malformed_path)
    malformed_payload = json.loads(malformed.output)
    assert malformed.exit_code == 3
    assert malformed_payload["error"]["code"] == "input_load_failed"

    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: (_ for _ in ()).throw(RuntimeError("provider canary")),
    )
    internal = _invoke("--edge", "058e29ff-57b9-47c8-b6fa-0914ac03e25c")
    internal_payload = json.loads(internal.output)
    assert internal.exit_code == 70
    assert internal_payload["error"]["code"] == "internal_error"
    assert "provider canary" not in internal.output
    assert internal.stderr == ""
    assert internal.output.count("\n") == 1


def test_check_more_than_1000_results_has_no_loss_or_duplication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    edges_path = tmp_path / "edges.yml"
    source = yaml.safe_load((EXAMPLES / "edges.yml").read_text(encoding="utf-8"))
    template = source["edges"][0]
    generated = []
    for index in range(1005):
        edge = deepcopy(template)
        edge["id"] = str(UUID(int=index + 1))
        generated.append(edge)
    source["edges"] = generated
    edges_path.write_text(yaml.safe_dump(source))
    monkeypatch.setattr(
        "infralink.cli.check.check_edge_health",
        lambda edge, resolver, timeout: _health(edge.id, healthy=True),
    )

    first_result = _invoke("--limit", "1000", edges_path=edges_path)
    first = json.loads(first_result.output)
    cursor = first["result"]["checks"]["page"]["next_cursor"]
    assert first_result.exit_code == 0
    assert len(first["result"]["checks"]["items"]) == 1000
    assert first["result"]["checks"]["page"] == {
        "limit": 1000,
        "returned": 1000,
        "total": 1005,
        "next_cursor": cursor,
    }
    assert first["result"]["summary"] == {"total": 1005, "healthy": 1005, "unhealthy": 0}

    second_result = _invoke(
        "--limit",
        "1000",
        "--cursor",
        cursor,
        edges_path=edges_path,
    )
    second = json.loads(second_result.output)
    assert second_result.exit_code == 0
    assert second["result"]["checks"]["page"] == {
        "limit": 1000,
        "returned": 5,
        "total": 1005,
        "next_cursor": None,
    }
    ids = [
        item["edge_id"]
        for payload in (first, second)
        for item in payload["result"]["checks"]["items"]
    ]
    assert len(ids) == len(set(ids)) == 1005
