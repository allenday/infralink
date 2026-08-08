from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from infralink.cli.main import cli
from infralink.release.contracts import ReleaseAttestationV1, ReleaseCandidateV1

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "examples" / "release"
SCHEMAS = ROOT / "src" / "infralink" / "schemas" / "release" / "v1"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_public_candidate_fixture_has_explicit_immutable_release_bindings() -> None:
    candidate = ReleaseCandidateV1.model_validate(_fixture("release-candidate.v1.json"))

    assert candidate.release.identity == "releases/core-v2/42"
    assert candidate.release.channel == "core-v2"
    assert candidate.release.sequence == 42
    assert candidate.registry_commit == "a" * 40
    assert candidate.controller_commit == "b" * 40
    assert candidate.artifacts[0].sha256 == "c" * 64
    assert candidate.consumers == ["citadel", "watchtower"]


def test_public_attestation_fixture_binds_candidate_facts_to_publisher_tag() -> None:
    attestation = ReleaseAttestationV1.model_validate(_fixture("release-attestation.v1.json"))

    assert attestation.release.identity == "releases/core-v2/42"
    assert attestation.publisher_receipt.provider == "woodpecker"
    assert attestation.tag.name == attestation.release.identity
    assert attestation.tag.object_sha1 == "d" * 40
    assert attestation.artifacts[0].sha256 == "c" * 64


@pytest.mark.parametrize(
    "fixture_name, model",
    [
        ("release-candidate.v1.json", ReleaseCandidateV1),
        ("release-attestation.v1.json", ReleaseAttestationV1),
    ],
)
def test_published_schema_accepts_the_public_fixture(
    fixture_name: str, model: type[ReleaseCandidateV1] | type[ReleaseAttestationV1]
) -> None:
    fixture = _fixture(fixture_name)
    schema_name = fixture_name.removesuffix(".json") + ".schema.json"
    schema = json.loads((SCHEMAS / schema_name).read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(fixture)
    assert model.model_validate(fixture)


@pytest.mark.parametrize(
    "field, value",
    [
        ("branch", "main"),
        ("ref", "refs/heads/main"),
    ],
)
def test_candidate_rejects_mutable_ref_authority(field: str, value: str) -> None:
    candidate = _fixture("release-candidate.v1.json")
    candidate[field] = value

    with pytest.raises(ValidationError):
        ReleaseCandidateV1.model_validate(candidate)


@pytest.mark.parametrize("field, value", [("branch", "main"), ("ref", "refs/heads/main")])
def test_published_candidate_schema_rejects_mutable_ref_authority(field: str, value: str) -> None:
    candidate = _fixture("release-candidate.v1.json")
    candidate[field] = value
    schema = json.loads((SCHEMAS / "release-candidate.v1.schema.json").read_text(encoding="utf-8"))

    assert list(Draft202012Validator(schema).iter_errors(candidate))


def test_candidate_rejects_release_identity_channel_or_sequence_mismatch() -> None:
    candidate = _fixture("release-candidate.v1.json")
    release = candidate["release"]
    assert isinstance(release, dict)
    release["sequence"] = 43

    with pytest.raises(ValidationError, match="identity must encode channel and sequence"):
        ReleaseCandidateV1.model_validate(candidate)


def test_attestation_rejects_a_tag_for_another_release() -> None:
    attestation = _fixture("release-attestation.v1.json")
    tag = attestation["tag"]
    assert isinstance(tag, dict)
    tag["name"] = "releases/core-v2/43"

    with pytest.raises(ValidationError, match="tag name must match release identity"):
        ReleaseAttestationV1.model_validate(attestation)


def test_merged_release_cli_accepts_public_producer_fixtures() -> None:
    candidate = FIXTURES / "release-candidate.v1.json"
    attestation = FIXTURES / "release-attestation.v1.json"

    candidate_result = CliRunner().invoke(
        cli, ["release", "validate-candidate", "--candidate", str(candidate)]
    )
    attestation_result = CliRunner().invoke(
        cli, ["release", "inspect-attestation", "--attestation", str(attestation)]
    )

    assert candidate_result.exit_code == 0, candidate_result.output
    assert attestation_result.exit_code == 0, attestation_result.output
