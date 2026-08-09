from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner
from jsonschema import Draft202012Validator

from infralink.cli.main import cli

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
HOST_ID = "d1b9e5d5-36b0-459d-a556-96622811fbd5"
EDGE_ID = "058e29ff-57b9-47c8-b6fa-0914ac03e25c"
OBSERVATION_ID = "declared-gatus-edge"


def _sources() -> list[str]:
    return [
        "--registry",
        str(EXAMPLES / "registry.yml"),
        "--edges",
        str(EXAMPLES / "edges.yml"),
    ]


def _observation_inputs(tmp_path: Path) -> tuple[Path, Path]:
    plan = tmp_path / "core-plan.json"
    plan.write_text(
        json.dumps(
            {
                "schema_version": "infralink.observation-plan/v1",
                "dependencies": [
                    {
                        "id": OBSERVATION_ID,
                        "source_service_id": "fa2b9872-d94c-4b20-a73a-57a205560769/app-worker",
                        "target_service_id": f"{HOST_ID}/postgresql",
                        "target_endpoint_id": f"{HOST_ID}/postgresql/tcp",
                        "required": True,
                        "execution_adapter": "gatus",
                        "health_signal_refs": [f"dependency/{OBSERVATION_ID}/health/reachable"],
                    }
                ],
                "service_profiles": [{"id": "postgres-database"}],
                "services": [{"id": f"{HOST_ID}/postgresql", "profile_id": "postgres-database"}],
            }
        ),
        encoding="utf-8",
    )
    bindings = tmp_path / "adapter-bindings.yml"
    bindings.write_text(
        yaml.safe_dump(
            {
                "schema_version": "infra-observe.adapter-bindings.v1",
                "bindings": [
                    {
                        "id": f"gatus-{OBSERVATION_ID}",
                        "renderer_kind": "gatus",
                        "observation_backend_id": "core-health",
                        "output_identity": OBSERVATION_ID,
                        "signal_ref": f"dependency/{OBSERVATION_ID}/health/reachable",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return plan, bindings


def _invoke(*args: str):
    return CliRunner().invoke(cli, ["--output", "json", *_sources(), "doctor", *args])


def test_doctor_is_discoverable_and_global_doctor_is_declaration_only() -> None:
    help_result = CliRunner().invoke(cli, ["help"])
    help_payload = yaml.safe_load(help_result.output)
    assert "doctor" in {child["name"] for child in help_payload["result"]["children"]}

    result = _invoke()
    payload = json.loads(result.output)
    assert result.exit_code == 0
    assert payload["result"]["target"]["type"] == "global"
    assert payload["result"]["status"] == "unknown"
    assert payload["result"]["reason"] == "no_observation_evidence"
    assert payload["result"]["evidence"] == []


def test_doctor_validate_host_uses_declared_gatus_coverage_without_network_calls(
    tmp_path: Path, monkeypatch
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    monkeypatch.setattr(
        "infralink.health.checks.check_edge_health",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("local probe forbidden")),
    )

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "host",
        "database.example.com",
        "--validate",
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["target"] == {
        "type": "host",
        "id": HOST_ID,
        "canonical_name": "database.example.com",
    }
    assert payload["result"]["coverage"] == {
        "required": 1,
        "bound": 1,
        "unbound": 0,
        "unsupported": 0,
        "valid": True,
    }
    assert payload["result"]["evidence"] == [
        {
            "id": OBSERVATION_ID,
            "adapter": "gatus",
            "signal_refs": [f"dependency/{OBSERVATION_ID}/health/reachable"],
            "status": "unknown",
            "reason": "no_live_observation_evidence",
        }
    ]
    assert payload["result"]["status"] == "unknown"
    assert payload["result"]["reason"] == "no_live_observation_evidence"
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/doctor.json").read_text())
    Draft202012Validator(schema).validate(payload)


def test_normal_doctor_never_claims_health_without_a_declared_live_observer(
    tmp_path: Path,
) -> None:
    plan, bindings = _observation_inputs(tmp_path)

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "edge",
        EDGE_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["status"] == "unknown"
    assert payload["result"]["reason"] == "no_live_observation_evidence"
    assert payload["result"]["evidence"][0]["id"] == OBSERVATION_ID
    assert payload["result"]["reason"] == "no_live_observation_evidence"
    assert payload["result"]["evidence"][0]["adapter"] == "gatus"
    prefix = ["infralink", "--output", "json", *_sources()]
    assert all(action["argv"][: len(prefix)] == prefix for action in payload["next_actions"])
    show = next(action for action in payload["next_actions"] if action["rel"] == "show")
    assert show["argv"][-3:] == ["edge", "show", EDGE_ID]
    assert CliRunner().invoke(cli, show["argv"][1:]).exit_code == 0


def test_doctor_profile_resolves_declared_observation_profile_not_service_or_role_name(
    tmp_path: Path,
) -> None:
    plan, bindings = _observation_inputs(tmp_path)

    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "profile",
        "postgres-database",
        "--validate",
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["target"] == {
        "type": "profile",
        "id": "postgres-database",
        "canonical_name": None,
    }
    assert payload["result"]["coverage"]["valid"] is True


def test_doctor_validate_requires_an_explicit_observation_plan() -> None:
    result = _invoke("host", "database.example.com", "--validate")
    payload = json.loads(result.output)

    assert result.exit_code == 2
    assert payload["error"]["code"] == "configuration_required"
    assert payload["error"]["details"] == {"source": "observation_plan"}


def test_doctor_observation_inputs_use_environment_defaults_and_flags_override(
    tmp_path: Path,
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    alternate_plan = tmp_path / "alternate-plan.json"
    alternate_bindings = tmp_path / "alternate-bindings.yml"
    alternate_plan.write_text('{"dependencies": []}', encoding="utf-8")
    alternate_bindings.write_text("bindings: []\n", encoding="utf-8")
    environment = {
        "INFRALINK_OBSERVATION_PLAN": str(plan),
        "INFRALINK_ADAPTER_BINDINGS": str(bindings),
    }

    from_environment = CliRunner().invoke(
        cli,
        ["--output", "json", *_sources(), "doctor", "host", "database.example.com", "--validate"],
        env=environment,
    )
    with_flags = CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            *_sources(),
            "doctor",
            "--observation-plan",
            str(alternate_plan),
            "--adapter-bindings",
            str(alternate_bindings),
            "host",
            "database.example.com",
            "--validate",
        ],
        env=environment,
    )

    environment_payload = json.loads(from_environment.output)
    override_payload = json.loads(with_flags.output)
    assert from_environment.exit_code == with_flags.exit_code == 0
    assert environment_payload["command"]["resolved"]["observation_plan"] == str(plan)
    assert environment_payload["command"]["resolved"]["adapter_bindings"] == str(bindings)
    assert override_payload["command"]["resolved"]["observation_plan"] == str(alternate_plan)
    assert override_payload["command"]["resolved"]["adapter_bindings"] == str(alternate_bindings)


def test_global_doctor_uses_supplied_observation_inputs(tmp_path: Path) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    result = _invoke("--observation-plan", str(plan), "--adapter-bindings", str(bindings))
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["coverage"] == {
        "required": 1,
        "bound": 1,
        "unbound": 0,
        "unsupported": 0,
        "valid": True,
    }
    assert payload["result"]["evidence"][0]["id"] == OBSERVATION_ID


def test_declared_dependency_edge_never_advertises_an_invalid_topology_show_action(
    tmp_path: Path,
) -> None:
    plan, bindings = _observation_inputs(tmp_path)
    result = _invoke(
        "--observation-plan",
        str(plan),
        "--adapter-bindings",
        str(bindings),
        "edge",
        OBSERVATION_ID,
    )
    payload = json.loads(result.output)

    assert result.exit_code == 0
    assert payload["result"]["target"]["id"] == OBSERVATION_ID
    assert "show" not in {action["rel"] for action in payload["next_actions"]}


def test_doctor_unknown_host_returns_a_bounded_canonical_discovery_action() -> None:
    result = _invoke("host", "missing-host")
    payload = json.loads(result.output)

    assert result.exit_code == 3
    assert payload["error"]["code"] == "entity_not_found"
    next_action = payload["next_actions"][0]
    assert next_action["rel"] == "list"
    assert next_action["argv"] == ["infralink", "--output", "json", *_sources(), "host", "list"]
    replay = CliRunner().invoke(cli, next_action["argv"][1:])
    assert replay.exit_code == 0
    assert json.loads(replay.output)["command"]["resolved"]["registry"] == str(
        EXAMPLES / "registry.yml"
    )
