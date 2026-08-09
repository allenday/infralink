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

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"


def validate_schema(payload: dict) -> None:
    schema = json.loads((ROOT / "src/infralink/schemas/cli/v1/validate.json").read_text())
    Draft202012Validator(schema).validate(payload)


def test_validate_returns_yaml_envelope_by_default():
    runner = CliRunner()
    result = runner.invoke(cli, ["--registry", "missing.yml", "validate"])
    payload = yaml.safe_load(result.output)
    assert payload["ok"] is False
    assert "error" in payload
    assert "fix" in payload
    assert result.exit_code == 3
    assert result.stderr == ""
    assert result.output.startswith("schema_version: infralink.cli/v1\n")
    assert payload["error"]["code"] == "input_load_failed"
    validate_schema(payload)


def test_validate_malformed_input_uses_central_load_failure(tmp_path: Path) -> None:
    malformed = tmp_path / "edges.yml"
    malformed.write_text("edges: [")
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(malformed),
            "validate",
        ],
    )
    payload = yaml.safe_load(result.output)

    assert result.exit_code == 3
    assert payload["error"]["code"] == "input_load_failed"
    assert result.stderr == ""


@pytest.mark.parametrize(
    "output_args",
    [("--output=yaml",), ("--output=json",), ("-o", "json")],
)
def test_validate_option_spellings_are_schema_equivalent(
    output_args: tuple[str, ...],
) -> None:
    result = CliRunner().invoke(
        cli,
        [
            *output_args,
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "validate",
        ],
    )
    payload = yaml.safe_load(result.output)
    assert result.stderr == ""
    validate_schema(payload)
    assert set(payload) == {
        "schema_version",
        "ok",
        "command",
        "result",
        "next_actions",
        "meta",
    }


def test_resolution_warnings_are_structured_without_stderr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    monkeypatch.setattr(
        EdgeResolver,
        "validate_all",
        lambda self: ([], ["canary warning"]),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "validate",
            "--check-resolution",
        ],
    )
    payload = yaml.safe_load(result.output)
    assert result.stderr == ""
    validate_schema(payload)
    assert payload["result"]["warnings"]["items"] == [
        {
            "code": "resolution_warning",
            "path": None,
            "message": "Resolution warning",
            "severity": "warning",
        }
    ]
    assert "canary warning" not in result.output


def test_validate_does_not_silently_truncate_101_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    warnings = [f"warning-{index}" for index in range(101)]
    monkeypatch.setattr(EdgeResolver, "validate_all", lambda self: ([], warnings))
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "validate",
            "--check-resolution",
        ],
    )
    payload = yaml.safe_load(result.output)
    warning_page = payload["result"]["warnings"]
    assert len(warning_page["items"]) == 20
    assert warning_page["page"]["returned"] == 20
    assert warning_page["page"]["total"] == 101
    assert warning_page["page"]["next_cursor"] is not None
    assert payload["meta"]["truncated"] is True


def _write_invalid_target_inputs(tmp_path: Path) -> tuple[Path, Path, str]:
    registry_path = tmp_path / "registry.yml"
    edges_path = tmp_path / "edges.yml"
    registry_path.write_text((EXAMPLES / "registry.yml").read_text(encoding="utf-8"))
    edges = yaml.safe_load((EXAMPLES / "edges.yml").read_text(encoding="utf-8"))
    edge_id = edges["edges"][0]["id"]
    edges["edges"][0]["to"]["host"] = "00000000-0000-4000-8000-000000000099"
    edges["edges"] = edges["edges"][:1]
    edges_path.write_text(yaml.safe_dump(edges))
    return registry_path, edges_path, edge_id


def _invoke_validate(
    registry_path: Path,
    edges_path: Path,
    *args: str,
):
    return CliRunner().invoke(
        cli,
        [
            "--output",
            "json",
            "--registry",
            str(registry_path),
            "--edges",
            str(edges_path),
            "validate",
            *args,
        ],
    )


def test_invalid_topology_is_completed_negative_result_with_stable_diagnostic(
    tmp_path: Path,
) -> None:
    registry_path, edges_path, edge_id = _write_invalid_target_inputs(tmp_path)

    result = _invoke_validate(registry_path, edges_path)
    payload = json.loads(result.output)

    assert result.exit_code == 1
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    assert payload["ok"] is True
    assert "error" not in payload
    assert payload["result"]["valid"] is False
    assert payload["result"]["errors"]["items"] == [
        {
            "code": "target_host_not_found",
            "path": f"edges.{edge_id}.to.host",
            "message": "Target host not found",
            "severity": "error",
        }
    ]
    validate_schema(payload)


def test_strict_warnings_are_completed_negative_but_non_strict_is_positive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    monkeypatch.setattr(EdgeResolver, "validate_all", lambda self: ([], ["provider canary"]))
    base = [
        "--registry",
        str(EXAMPLES / "registry.yml"),
        "--edges",
        str(EXAMPLES / "edges.yml"),
        "validate",
        "--check-resolution",
    ]

    non_strict = CliRunner().invoke(cli, base)
    strict = CliRunner().invoke(cli, [*base, "--strict"])
    non_strict_payload = yaml.safe_load(non_strict.output)
    strict_payload = yaml.safe_load(strict.output)

    assert non_strict.exit_code == 0
    assert non_strict_payload["result"]["valid"] is True
    assert strict.exit_code == 1
    assert strict_payload["ok"] is True
    assert strict_payload["result"]["valid"] is False
    assert strict_payload["result"]["warnings"]["items"][0] == {
        "code": "resolution_warning",
        "path": None,
        "message": "Resolution warning",
        "severity": "warning",
    }
    assert "provider canary" not in strict.output


def test_validate_pages_selected_collection_and_replays_all_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    registry_path, edges_path, _ = _write_invalid_target_inputs(tmp_path)
    monkeypatch.setenv("INFRALINK_REGISTRY", str(registry_path))
    monkeypatch.setenv("INFRALINK_EDGES", str(edges_path))
    monkeypatch.setattr(
        EdgeResolver,
        "validate_all",
        lambda self: ([], [f"warning-{index}" for index in range(3)]),
    )
    first = json.loads(
        _invoke_validate(
            registry_path,
            edges_path,
            "--strict",
            "--check-resolution",
            "--limit",
            "1",
        ).output
    )

    assert first["result"]["errors"]["page"]["returned"] == 1
    assert first["result"]["warnings"]["page"]["returned"] == 1
    warning_cursor = first["result"]["warnings"]["page"]["next_cursor"]
    action = next(
        item
        for item in first["next_actions"]
        if item["rel"] == "continue"
        and "--collection warnings" in item["command"]
    )
    assert action["command"].endswith("validate --strict --check-resolution --collection warnings --cursor '{cursor}' --limit 1")
    replay = [warning_cursor if item == "{cursor}" else item for item in shlex.split(action["command"])]
    second = yaml.safe_load(CliRunner().invoke(cli, replay[1:]).output)
    assert second["result"]["warnings"]["page"]["returned"] == 1
    assert second["result"]["warnings"]["page"]["total"] == 3
    assert second["result"]["errors"]["items"] == first["result"]["errors"]["items"]
    assert second["result"]["summary"] == {"error_count": 1, "warning_count": 3}
    assert second["result"]["valid"] is False


def test_validate_cursor_requires_collection_and_binds_flags_and_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    registry_path, edges_path, _ = _write_invalid_target_inputs(tmp_path)
    monkeypatch.setattr(
        EdgeResolver,
        "validate_all",
        lambda self: ([], ["warning-0", "warning-1"]),
    )
    first = json.loads(
        _invoke_validate(
            registry_path,
            edges_path,
            "--check-resolution",
            "--limit",
            "1",
        ).output
    )
    cursor = first["result"]["warnings"]["page"]["next_cursor"]

    for extra in [
        ("--cursor", cursor),
        ("--collection", "warnings", "--cursor", cursor, "--strict"),
    ]:
        result = _invoke_validate(
            registry_path,
            edges_path,
            "--check-resolution",
            "--limit",
            "1",
            *extra,
        )
        assert result.exit_code == 2
        assert json.loads(result.output)["error"]["code"] == "invalid_cursor"

    edges = yaml.safe_load(edges_path.read_text(encoding="utf-8"))
    edges["edges"][0]["to"]["port"] = 5433
    edges_path.write_text(yaml.safe_dump(edges))
    stale = _invoke_validate(
        registry_path,
        edges_path,
        "--check-resolution",
        "--limit",
        "1",
        "--collection",
        "warnings",
        "--cursor",
        cursor,
    )
    assert stale.exit_code == 2
    assert json.loads(stale.output)["error"]["code"] == "invalid_cursor"


def test_validate_unexpected_resolution_error_uses_central_internal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infralink.core.resolver import EdgeResolver

    monkeypatch.setattr(
        EdgeResolver,
        "validate_all",
        lambda self: (_ for _ in ()).throw(RuntimeError("provider canary")),
    )
    result = CliRunner().invoke(
        cli,
        [
            "--registry",
            str(EXAMPLES / "registry.yml"),
            "--edges",
            str(EXAMPLES / "edges.yml"),
            "validate",
            "--check-resolution",
        ],
    )
    payload = yaml.safe_load(result.output)

    assert result.exit_code == 70
    assert payload["error"]["code"] == "internal_error"
    assert "provider canary" not in result.output
    assert result.stderr == ""


def test_validate_more_than_1000_diagnostics_has_no_loss_or_duplication(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.yml"
    edges_path = tmp_path / "edges.yml"
    registry_path.write_text((EXAMPLES / "registry.yml").read_text(encoding="utf-8"))
    source = yaml.safe_load((EXAMPLES / "edges.yml").read_text(encoding="utf-8"))
    template = source["edges"][0]
    generated = []
    for index in range(1005):
        edge = deepcopy(template)
        edge["id"] = str(UUID(int=index + 1))
        edge["to"]["host"] = "00000000-0000-4000-8000-000000000099"
        generated.append(edge)
    source["edges"] = generated
    edges_path.write_text(yaml.safe_dump(source))

    first_result = _invoke_validate(registry_path, edges_path, "--limit", "1000")
    first = json.loads(first_result.output)
    cursor = first["result"]["errors"]["page"]["next_cursor"]
    assert first_result.exit_code == 1
    assert len(first["result"]["errors"]["items"]) == 1000
    assert first["result"]["errors"]["page"] == {
        "limit": 1000,
        "returned": 1000,
        "total": 1005,
        "next_cursor": cursor,
    }
    assert first["meta"]["truncated"] is True

    second_result = _invoke_validate(
        registry_path,
        edges_path,
        "--limit",
        "1000",
        "--collection",
        "errors",
        "--cursor",
        cursor,
    )
    second = json.loads(second_result.output)
    assert second_result.exit_code == 1
    assert second["result"]["errors"]["page"] == {
        "limit": 1000,
        "returned": 5,
        "total": 1005,
        "next_cursor": None,
    }
    paths = [
        item["path"] for payload in (first, second) for item in payload["result"]["errors"]["items"]
    ]
    assert len(paths) == len(set(paths)) == 1005
