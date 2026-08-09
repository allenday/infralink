from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from infralink.cli.main import cli
from infralink.release.contracts import (
    PublisherRequestV2,
    PublisherRequestV3,
    ReleaseAttestationV1,
    ReleaseAttestationV2,
    ReleaseAttestationV3,
    ReleaseCandidateV1,
    parse_publisher_request_v2_json,
    parse_publisher_request_v3_json,
    parse_release_attestation_v2_json,
    parse_release_attestation_v3_json,
)

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "examples" / "release"
SCHEMAS = ROOT / "src" / "infralink" / "schemas" / "release" / "v1"
V2_SCHEMAS = ROOT / "src" / "infralink" / "schemas" / "release" / "v2"
V2JsonParser = Callable[[str], PublisherRequestV2 | ReleaseAttestationV2]
V3JsonParser = Callable[[str], PublisherRequestV3 | ReleaseAttestationV3]


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _bind_request_digest(request: dict[str, object]) -> None:
    request_without_digest = {
        key: value for key, value in request.items() if key != "request_digest"
    }
    request["request_digest"] = hashlib.sha256(
        json.dumps(
            request_without_digest, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
    ).hexdigest()


def _v3_request() -> dict[str, object]:
    request = _fixture("publisher-request.v2.json")
    request["schema_version"] = "infralink.publisher-request.v3"
    remote = "https://example.com/relaxgg/infra-registry"
    receipt = request["ci_receipt"]
    assert isinstance(receipt, dict)
    receipt["repository"] = remote
    request["release_manifest"] = {
        "repository": remote,
        "blob_identity": f"{remote}/blobs/{'a' * 40}",
        "sha256": "b" * 64,
        "authority": json.loads(
            json.dumps(
                {
                    key: value
                    for key, value in request.items()
                    if key
                    in {
                        "release",
                        "registry_commit",
                        "controller_commit",
                        "ci_receipt",
                        "artifacts",
                        "publisher",
                    }
                }
            )
        ),
    }
    _bind_request_digest(request)
    return request


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


def test_v2_request_fixture_has_canonical_digest_and_immutable_sources() -> None:
    request = PublisherRequestV2.model_validate(_fixture("publisher-request.v2.json"))

    assert request.request_digest == request.canonical_digest()
    assert request.ci_receipt.source_identity == "woodpecker://example.com/registry/pipelines/576"
    assert request.artifacts[0].source_digest == "e" * 64
    assert request.publisher.image.endswith("@sha256:" + "f" * 64)
    assert request.mode == "dry-run"


def test_v2_attestation_fixture_binds_the_canonical_request_digest() -> None:
    attestation = ReleaseAttestationV2.model_validate(_fixture("release-attestation.v2.json"))

    assert attestation.request_digest == attestation.request.canonical_digest()
    assert attestation.result == "dry-run"
    assert attestation.tag is None


def test_v3_request_binds_a_canonical_immutable_release_manifest() -> None:
    request = PublisherRequestV3.model_validate(_v3_request())

    assert request.request_digest == request.canonical_digest()
    assert request.release_manifest.blob_identity.endswith("/blobs/" + "a" * 40)
    assert request.release_manifest.sha256 == "b" * 64
    assert request.release_manifest.authority.release == request.release


def test_v3_published_schema_and_strict_parser_accept_the_public_fixture() -> None:
    fixture_path = FIXTURES / "publisher-request.v3.json"
    fixture = _fixture(fixture_path.name)
    schema = json.loads(
        (V2_SCHEMAS.parent / "v3" / "publisher-request.v3.schema.json").read_text(encoding="utf-8")
    )

    Draft202012Validator(schema).validate(fixture)
    assert parse_publisher_request_v3_json(fixture_path.read_text(encoding="utf-8"))


def test_v3_attestation_carries_the_bound_v3_request() -> None:
    attestation = _fixture("release-attestation.v2.json")
    request = _v3_request()
    attestation["schema_version"] = "infralink.release-attestation.v3"
    attestation["request"] = request
    attestation["request_digest"] = request["request_digest"]

    parsed = ReleaseAttestationV3.model_validate(attestation)

    assert parsed.request.release_manifest.repository == parsed.request.ci_receipt.repository
    assert parse_release_attestation_v3_json(json.dumps(attestation, separators=(",", ":")))


@pytest.mark.parametrize(
    ("field", "value", "match"),
    [
        ("blob_identity", "release-candidates/core-v2/1/release-manifest.json", "blob_identity"),
        (
            "blob_identity",
            "https://gitea.i.cyberstorm.dev/relaxgg/infra-registry.git/blobs/main",
            "blob_identity",
        ),
        (
            "blob_identity",
            "https://gitea.i.cyberstorm.dev/relaxgg/infra-registry.git/blobs/" + "A" * 40,
            "blob_identity",
        ),
        ("sha256", "0" * 63, "sha256"),
    ],
)
def test_v3_request_rejects_path_only_mutable_or_noncanonical_manifest_identity(
    field: str, value: str, match: str
) -> None:
    request = _v3_request()
    manifest = request["release_manifest"]
    assert isinstance(manifest, dict)
    manifest[field] = value
    _bind_request_digest(request)

    with pytest.raises(ValidationError, match=match):
        PublisherRequestV3.model_validate(request)


def test_v3_request_rejects_a_manifest_from_another_registry_authority() -> None:
    request = _v3_request()
    manifest = request["release_manifest"]
    assert isinstance(manifest, dict)
    other_remote = "https://example.com/relaxgg/another-registry"
    manifest["repository"] = other_remote
    manifest["blob_identity"] = f"{other_remote}/blobs/{'a' * 40}"
    _bind_request_digest(request)

    with pytest.raises(ValidationError, match="manifest repository must match request authority"):
        PublisherRequestV3.model_validate(request)


@pytest.mark.parametrize(
    "mutate",
    [
        lambda request: request["release"].update(
            {"identity": "releases/core-v2/43", "sequence": 43}
        ),
        lambda request: request.update({"registry_commit": "c" * 40}),
        lambda request: request.update({"controller_commit": "c" * 40}),
        lambda request: request["ci_receipt"].update({"run": "577"}),
        lambda request: request["artifacts"][0].update({"sha256": "d" * 64}),
        lambda request: request["publisher"].update({"identity": "another-publisher"}),
    ],
)
def test_v3_request_rejects_manifest_authority_that_disagrees_with_request(
    mutate: Callable[[dict[str, object]], None],
) -> None:
    request = _v3_request()
    mutate(request)
    _bind_request_digest(request)

    with pytest.raises(ValidationError, match="manifest authority must match request authority"):
        PublisherRequestV3.model_validate(request)


def test_v3_request_rejects_a_missing_manifest_binding() -> None:
    request = _v3_request()
    request.pop("release_manifest")
    _bind_request_digest(request)

    with pytest.raises(ValidationError, match="release_manifest"):
        PublisherRequestV3.model_validate(request)


def test_v3_published_schema_rejects_noncanonical_manifest_locator() -> None:
    request = _v3_request()
    manifest = request["release_manifest"]
    assert isinstance(manifest, dict)
    manifest["blob_identity"] = "release-candidates/core-v2/1/release-manifest.json"
    _bind_request_digest(request)
    schema = json.loads(
        (V2_SCHEMAS.parent / "v3" / "publisher-request.v3.schema.json").read_text(encoding="utf-8")
    )

    assert list(Draft202012Validator(schema).iter_errors(request))


def test_v2_request_remains_compatible_without_signer_policy_binding() -> None:
    request = PublisherRequestV2.model_validate(_fixture("publisher-request.v2.json"))

    assert request.schema_version == "infralink.publisher-request.v2"


@pytest.mark.parametrize(
    ("fixture_name", "parser"),
    [
        ("publisher-request.v2.json", parse_publisher_request_v2_json),
        ("release-attestation.v2.json", parse_release_attestation_v2_json),
    ],
)
def test_v2_json_parsers_accept_public_fixtures(fixture_name: str, parser: V2JsonParser) -> None:
    assert parser((FIXTURES / fixture_name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fixture_name", "parser", "duplicate_member"),
    [
        (
            "publisher-request.v2.json",
            parse_publisher_request_v2_json,
            '  "schema_version": "infralink.publisher-request.v2",\n',
        ),
        (
            "publisher-request.v2.json",
            parse_publisher_request_v2_json,
            '    "identity": "infralink-release-publisher-woodpecker",\n',
        ),
        (
            "release-attestation.v2.json",
            parse_release_attestation_v2_json,
            '  "schema_version": "infralink.release-attestation.v2",\n',
        ),
        (
            "release-attestation.v2.json",
            parse_release_attestation_v2_json,
            '      "identity": "infralink-release-publisher-woodpecker",\n',
        ),
    ],
)
def test_v2_json_parsers_reject_duplicate_object_members(
    fixture_name: str,
    parser: V2JsonParser,
    duplicate_member: str,
) -> None:
    document = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    duplicate_document = document.replace(duplicate_member, duplicate_member * 2, 1)

    with pytest.raises(ValueError, match="duplicate JSON object key"):
        parser(duplicate_document)


@pytest.mark.parametrize(
    "fixture_name, model, schema_name",
    [
        ("publisher-request.v2.json", PublisherRequestV2, "publisher-request.v2.schema.json"),
        ("release-attestation.v2.json", ReleaseAttestationV2, "release-attestation.v2.schema.json"),
    ],
)
def test_published_v2_schema_accepts_public_fixture(
    fixture_name: str,
    model: type[PublisherRequestV2] | type[ReleaseAttestationV2],
    schema_name: str,
) -> None:
    fixture = _fixture(fixture_name)
    schema = json.loads((V2_SCHEMAS / schema_name).read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(fixture)
    assert model.model_validate(fixture)


@pytest.mark.parametrize(
    "field, value",
    [
        ("request_digest", "0" * 64),
        ("registry_commit", "main"),
        ("publisher", {"identity": "publisher", "image": "publisher:latest"}),
    ],
)
def test_v2_request_rejects_noncanonical_mutable_or_unpinned_input(
    field: str, value: object
) -> None:
    request = _fixture("publisher-request.v2.json")
    request[field] = value

    with pytest.raises(ValidationError):
        PublisherRequestV2.model_validate(request)


def test_v2_request_rejects_unknown_and_secret_bearing_source_fields() -> None:
    request = _fixture("publisher-request.v2.json")
    receipt = request["ci_receipt"]
    assert isinstance(receipt, dict)
    receipt["token"] = "not-a-secret"

    with pytest.raises(ValidationError):
        PublisherRequestV2.model_validate(request)


def test_v2_attestation_requires_a_tag_only_when_published() -> None:
    attestation = _fixture("release-attestation.v2.json")
    request = attestation["request"]
    assert isinstance(request, dict)
    request["mode"] = "publish"
    request_without_digest = {
        key: value for key, value in request.items() if key != "request_digest"
    }
    request["request_digest"] = hashlib.sha256(
        json.dumps(
            request_without_digest, ensure_ascii=True, separators=(",", ":"), sort_keys=True
        ).encode("ascii")
    ).hexdigest()
    attestation["request_digest"] = request["request_digest"]
    attestation["result"] = "published"

    with pytest.raises(ValidationError, match="published attestation requires a tag"):
        ReleaseAttestationV2.model_validate(attestation)
