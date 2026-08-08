from __future__ import annotations

import json
from pathlib import Path

import yaml
from click.testing import CliRunner

from infralink.cli.main import cli
from tests.cli_helpers import assert_schema

REGISTRY_COMMIT = "a" * 40
CONTROLLER_COMMIT = "b" * 40


def _validation(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "schema_version": "infralink.release-validation.v1",
        "release_identity": "releases/core-v2/42",
        "registry_commit": REGISTRY_COMMIT,
        "controller_commit": CONTROLLER_COMMIT,
        "annotated": True,
        "status": "active",
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _admission(path: Path, payload: dict[str, object] | None = None) -> Path:
    path.write_text(
        yaml.safe_dump(
            payload
            or {
                "schema_version": "infralink.release-admission.v1",
                "selection": {
                    "mode": "release-channel",
                    "channel": "core-v2",
                    "recent_window": 20,
                    "maximum_candidates": 5,
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return path


def _candidate(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "schema_version": "infralink.release-candidate.v1",
        "release_identity": "releases/core-v2/42",
        "registry_commit": REGISTRY_COMMIT,
        "controller_commit": CONTROLLER_COMMIT,
        "ci_receipt": {
            "provider": "woodpecker",
            "repository": "relaxgg/infra-registry",
            "run": "576",
        },
        "artifacts": [
            {
                "path": "release/runtime.tar.gz",
                "sha256": "c" * 64,
            }
        ],
        "consumers": ["citadel", "watchtower"],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _attestation(path: Path, **overrides: object) -> Path:
    payload: dict[str, object] = {
        "schema_version": "infralink.release-attestation.v1",
        "release_identity": "releases/core-v2/42",
        "registry_commit": REGISTRY_COMMIT,
        "controller_commit": CONTROLLER_COMMIT,
        "publisher_receipt": {"provider": "woodpecker", "run": "600"},
        "tag": "releases/core-v2/42",
        "consumers": ["citadel", "watchtower"],
    }
    payload.update(overrides)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _payload(result) -> dict[str, object]:
    assert result.stderr == ""
    assert result.output.count("\n") == 1
    return json.loads(result.output)


def test_release_validate_candidate_preserves_manual_image_tag_sha_workflow(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path / "candidate.json")

    result = CliRunner().invoke(
        cli, ["release", "validate-candidate", "--candidate", str(candidate)]
    )

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert_schema(payload, "release-validate-candidate")
    assert payload["command"]["parsed"] == {
        "path": ["release", "validate-candidate"],
        "args": {},
        "flags": ["--candidate"],
    }
    assert payload["result"] == {
        "candidate": {
            "identity": "releases/core-v2/42",
            "registry_commit": REGISTRY_COMMIT,
            "controller_commit": CONTROLLER_COMMIT,
            "ci_receipt": {
                "provider": "woodpecker",
                "repository": "relaxgg/infra-registry",
                "run": "576",
            },
            "artifacts": [{"path": "release/runtime.tar.gz", "sha256": "c" * 64}],
            "consumers": ["citadel", "watchtower"],
        }
    }
    assert [item["rel"] for item in payload["next_actions"]] == ["render-publisher-request"]
    action = payload["next_actions"][0]
    assert action["templated"] is True
    assert action["bindings"] == {
        "admission": {
            "type": "string",
            "required": True,
            "source": "local release admission policy path",
        }
    }


def test_release_render_publisher_request_binds_only_immutable_candidate_inputs(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path / "candidate.json")
    admission = _admission(
        tmp_path / "admission.yml",
        {
            "schema_version": "infralink.release-admission.v1",
            "selection": {
                "mode": "release-channel",
                "channel": "core-v2",
                "recent_window": 20,
                "maximum_candidates": 5,
            },
            "publisher": {"state": "eligible", "provider": "woodpecker"},
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "render-publisher-request",
            "--candidate",
            str(candidate),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert_schema(payload, "release-render-publisher-request")
    request = payload["result"]["publisher_request"]
    assert request["schema_version"] == "infralink.publisher-request.v1"
    assert request["release_identity"] == "releases/core-v2/42"
    assert request["channel"] == "core-v2"
    assert request["sequence"] == 42
    assert request["registry_commit"] == REGISTRY_COMMIT
    assert request["controller_commit"] == CONTROLLER_COMMIT
    assert request["artifacts"] == [{"path": "release/runtime.tar.gz", "sha256": "c" * 64}]
    assert "branch" not in request
    assert "ref" not in request
    assert [item["rel"] for item in payload["next_actions"]] == ["inspect-attestation"]
    action = payload["next_actions"][0]
    assert action["templated"] is True
    assert action["bindings"] == {
        "attestation": {
            "type": "string",
            "required": True,
            "source": "trusted publisher completion record path",
        }
    }


def test_release_render_publisher_request_is_clear_no_go_when_publisher_is_unavailable(
    tmp_path: Path,
) -> None:
    candidate = _candidate(tmp_path / "candidate.json")
    admission = _admission(tmp_path / "admission.yml")

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "render-publisher-request",
            "--candidate",
            str(candidate),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 1
    payload = _payload(result)
    assert payload["error"]["code"] == "release_publisher_unavailable"
    assert "protected publisher" in payload["fix"].casefold()


def test_release_inspect_attestation_reports_consumer_shadow_actions(tmp_path: Path) -> None:
    attestation = _attestation(tmp_path / "attestation.json")

    result = CliRunner().invoke(
        cli, ["release", "inspect-attestation", "--attestation", str(attestation)]
    )

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert_schema(payload, "release-inspect-attestation")
    assert payload["result"]["attestation"]["tag"] == "releases/core-v2/42"
    assert payload["result"]["attestation"]["consumers"] == ["citadel", "watchtower"]
    assert payload["next_actions"] == []


def test_release_validate_candidate_rejects_mutable_branch_authority(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path / "candidate.json", branch="main")

    result = CliRunner().invoke(
        cli, ["release", "validate-candidate", "--candidate", str(candidate)]
    )

    assert result.exit_code == 3
    payload = _payload(result)
    assert payload["error"]["code"] == "release_candidate_invalid"


def test_release_validate_candidate_bounds_each_consumer_name(tmp_path: Path) -> None:
    candidate = _candidate(tmp_path / "candidate.json", consumers=["c" * 129])

    result = CliRunner().invoke(
        cli, ["release", "validate-candidate", "--candidate", str(candidate)]
    )

    assert result.exit_code == 3
    assert _payload(result)["error"]["code"] == "release_candidate_invalid"


def test_release_inspect_reports_admitted_release_and_publisher_unavailable(tmp_path: Path) -> None:
    validation = _validation(tmp_path / "release-validation.json")
    admission = _admission(tmp_path / "release-admission.yml")

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "inspect",
            "--release-validation",
            str(validation),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert_schema(payload, "release-inspect")
    assert payload["command"]["parsed"] == {
        "path": ["release", "inspect"],
        "args": {},
        "flags": ["--release-validation", "--admission"],
    }
    release = payload["result"]["release"]
    assert release["identity"] == "releases/core-v2/42"
    assert release["registry_commit"] == REGISTRY_COMMIT
    assert release["controller_commit"] == CONTROLLER_COMMIT
    assert payload["result"]["admission"]["state"] == "admitted"
    assert payload["result"]["provenance"] == {
        "validation_schema_version": "infralink.release-validation.v1",
        "source": "release-validation",
    }
    assert payload["result"]["compatibility"] == {
        "selection_mode": "release-channel",
        "controller_commit": CONTROLLER_COMMIT,
    }
    assert payload["result"]["publisher"]["state"] == "unavailable"
    assert [action["rel"] for action in payload["next_actions"]] == ["inspect"]


def test_release_inspect_reports_provider_eligibility_without_advertising_mutation(
    tmp_path: Path,
) -> None:
    validation = _validation(tmp_path / "release-validation.json")
    admission = _admission(
        tmp_path / "release-admission.yml",
        {
            "schema_version": "infralink.release-admission.v1",
            "selection": {
                "mode": "release-channel",
                "channel": "core-v2",
                "recent_window": 20,
                "maximum_candidates": 5,
            },
            "publisher": {"state": "eligible", "provider": "woodpecker"},
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "inspect",
            "--release-validation",
            str(validation),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["result"]["publisher"] == {"state": "eligible", "provider": "woodpecker"}
    assert [action["rel"] for action in payload["next_actions"]] == ["inspect"]


def test_release_inspect_rejects_unbounded_or_mismatched_admission_inputs(tmp_path: Path) -> None:
    validation = _validation(tmp_path / "release-validation.json")
    admission = _admission(
        tmp_path / "release-admission.yml",
        {
            "schema_version": "infralink.release-admission.v1",
            "selection": {
                "mode": "release-channel",
                "channel": "other",
                "recent_window": 20,
                "maximum_candidates": 5,
            },
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "inspect",
            "--release-validation",
            str(validation),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 3
    payload = _payload(result)
    assert payload["error"]["code"] == "release_admission_rejected"
    assert payload["error"]["details"]["release_channel"] == "core-v2"
    assert payload["error"]["details"]["admission_channel"] == "other"


def test_release_inspect_rejects_unknown_validation_version_without_publishing(
    tmp_path: Path,
) -> None:
    validation = _validation(
        tmp_path / "release-validation.json", schema_version="infralink.release-validation.v99"
    )
    admission = _admission(tmp_path / "release-admission.yml")

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "inspect",
            "--release-validation",
            str(validation),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 3
    payload = _payload(result)
    assert payload["error"]["code"] == "release_validation_invalid"
    assert any(action["rel"] == "help" for action in payload["next_actions"])


def test_release_inspect_marks_a_revoked_release_not_admitted(tmp_path: Path) -> None:
    validation = _validation(tmp_path / "release-validation.json", status="revoked")
    admission = _admission(
        tmp_path / "release-admission.yml",
        {
            "schema_version": "infralink.release-admission.v1",
            "selection": {
                "mode": "release-channel",
                "channel": "core-v2",
                "recent_window": 20,
                "maximum_candidates": 5,
            },
            "publisher": {"state": "eligible", "provider": "woodpecker"},
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "inspect",
            "--release-validation",
            str(validation),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = _payload(result)
    assert payload["result"]["release"]["status"] == "revoked"
    assert payload["result"]["admission"] == {
        "state": "not-admitted",
        "selection": {
            "mode": "release-channel",
            "channel": "core-v2",
            "recent_window": 20,
            "maximum_candidates": 5,
            "registry_commit": None,
        },
        "reason": "revoked",
    }


def test_release_inspect_rejects_credential_bearing_raw_revision_remote(tmp_path: Path) -> None:
    validation = _validation(tmp_path / "release-validation.json")
    admission = _admission(
        tmp_path / "release-admission.yml",
        {
            "schema_version": "infralink.release-admission.v1",
            "selection": {
                "mode": "raw-revision",
                "registry": {
                    "remote": "https://user:secret@example.invalid/registry.git",
                    "commit": REGISTRY_COMMIT,
                },
            },
        },
    )

    result = CliRunner().invoke(
        cli,
        [
            "release",
            "inspect",
            "--release-validation",
            str(validation),
            "--admission",
            str(admission),
        ],
    )

    assert result.exit_code == 3
    payload = _payload(result)
    assert payload["error"]["code"] == "release_admission_rejected"
