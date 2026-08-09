"""Typed inputs emitted by registry CI around the trusted publisher boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePath
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CHANNEL = r"^[a-z0-9][a-z0-9-]{0,62}$"
_IDENTITY = r"^releases/[a-z0-9][a-z0-9-]{0,62}/[1-9][0-9]*$"
_COMMIT = r"^[0-9a-f]{40}$"
_SHA256 = r"^[0-9a-f]{64}$"
_SOURCE_IDENTITY = r"^[a-z][a-z0-9+.-]{0,31}://[A-Za-z0-9._~:/@+-]{1,384}$"
_OCI_IMAGE_DIGEST = r"^[a-z0-9][a-z0-9._/-]{0,383}@sha256:[0-9a-f]{64}$"
_CANONICAL_GIT_REMOTE = r"^(?:https|ssh)://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._-]+)+$"
_IMMUTABLE_GIT_BLOB = r"^(?:https|ssh)://[A-Za-z0-9.-]+(?:/[A-Za-z0-9._-]+)*/blobs/[0-9a-f]{40}$"


class DuplicateJsonKeyError(ValueError):
    """Raised when a strict v2 contract document repeats an object member."""


def _reject_duplicate_object_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    value: dict[str, object] = {}
    for key, member in pairs:
        if key in value:
            raise DuplicateJsonKeyError(f"duplicate JSON object key: {key!r}")
        value[key] = member
    return value


def _load_strict_v2_json(document: str | bytes | bytearray) -> object:
    """Decode a v2 contract document without silently resolving duplicate keys."""
    return json.loads(document, object_pairs_hook=_reject_duplicate_object_members)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ReleaseIdentityV1(_Contract):
    """Immutable release identity, expressed redundantly for simple CI consumers."""

    identity: str = Field(pattern=_IDENTITY)
    channel: str = Field(pattern=_CHANNEL)
    sequence: int = Field(ge=1)

    @model_validator(mode="after")
    def identity_matches_parts(self) -> ReleaseIdentityV1:
        if self.identity != f"releases/{self.channel}/{self.sequence}":
            raise ValueError("identity must encode channel and sequence")
        return self


class CiReceiptV1(_Contract):
    provider: str = Field(min_length=1, max_length=128)
    repository: str = Field(min_length=1, max_length=256)
    run: str = Field(min_length=1, max_length=128)


class ArtifactBindingV1(_Contract):
    path: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=_SHA256)

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePath(value)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError("artifact path must be safe and relative")
        return value


ConsumerId = Annotated[str, Field(pattern=_CHANNEL, min_length=1, max_length=63)]


class _ReleaseEvidenceV1(_Contract):
    release: ReleaseIdentityV1
    registry_commit: str = Field(pattern=_COMMIT)
    controller_commit: str = Field(pattern=_COMMIT)
    ci_receipt: CiReceiptV1
    artifacts: list[ArtifactBindingV1] = Field(min_length=1, max_length=64)
    consumers: list[ConsumerId] = Field(min_length=1, max_length=64)

    @field_validator("artifacts")
    @classmethod
    def artifacts_have_unique_paths(cls, value: list[ArtifactBindingV1]) -> list[ArtifactBindingV1]:
        if len({artifact.path for artifact in value}) != len(value):
            raise ValueError("artifact paths must be unique")
        return value

    @field_validator("consumers")
    @classmethod
    def consumers_are_unique(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("consumers must be unique")
        return value


class ReleaseCandidateV1(_ReleaseEvidenceV1):
    """Immutable CI output used to render a trusted publisher request."""

    schema_version: Literal["infralink.release-candidate.v1"]


class PublisherReceiptV1(CiReceiptV1):
    """Bounded receipt for the dedicated protected publisher run."""


class ReleaseTagV1(_Contract):
    name: str = Field(pattern=_IDENTITY)
    object_sha1: str = Field(pattern=_COMMIT)


class ReleaseAttestationV1(_ReleaseEvidenceV1):
    """Immutable publisher completion record, including the created tag object."""

    schema_version: Literal["infralink.release-attestation.v1"]
    publisher_receipt: PublisherReceiptV1
    tag: ReleaseTagV1

    @model_validator(mode="after")
    def tag_matches_release(self) -> ReleaseAttestationV1:
        if self.tag.name != self.release.identity:
            raise ValueError("tag name must match release identity")
        return self


class ImmutableSourceReceiptV2(_Contract):
    """A receipt whose source cannot be silently retargeted."""

    provider: str = Field(min_length=1, max_length=128)
    repository: str = Field(min_length=1, max_length=256)
    run: str = Field(min_length=1, max_length=128)
    source_identity: str = Field(pattern=_SOURCE_IDENTITY, max_length=512)
    source_digest: str = Field(pattern=_SHA256)


class ArtifactSourceBindingV2(ArtifactBindingV1):
    """A release artifact and the immutable source from which it was obtained."""

    source_identity: str = Field(pattern=_SOURCE_IDENTITY, max_length=512)
    source_digest: str = Field(pattern=_SHA256)


class PublisherIdentityV2(_Contract):
    """The protected publisher identity and immutable runner image."""

    identity: ConsumerId
    image: str = Field(pattern=_OCI_IMAGE_DIGEST, max_length=512)


class ReleaseManifestAuthorityV1(_Contract):
    """The request facts which a canonical release manifest must carry."""

    release: ReleaseIdentityV1
    registry_commit: str = Field(pattern=_COMMIT)
    controller_commit: str = Field(pattern=_COMMIT)
    ci_receipt: ImmutableSourceReceiptV2
    artifacts: list[ArtifactSourceBindingV2] = Field(min_length=1, max_length=64)
    publisher: PublisherIdentityV2


class ReleaseManifestBindingV1(_Contract):
    """Exact canonical registry blob and its declared request authority."""

    repository: str = Field(pattern=_CANONICAL_GIT_REMOTE, max_length=512)
    blob_identity: str = Field(pattern=_IMMUTABLE_GIT_BLOB, max_length=640)
    sha256: str = Field(pattern=_SHA256)
    authority: ReleaseManifestAuthorityV1

    @field_validator("repository")
    @classmethod
    def canonical_registry_remote(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("repository must be a credential-free canonical Git remote")
        return value

    @model_validator(mode="after")
    def blob_identity_is_exact_immutable_registry_blob(self) -> ReleaseManifestBindingV1:
        if not self.blob_identity.startswith(f"{self.repository}/blobs/"):
            raise ValueError("blob identity must name an exact immutable registry blob")
        return self


class PublisherRequestV2(_Contract):
    """Canonical input for one protected publisher invocation."""

    schema_version: Literal["infralink.publisher-request.v2"]
    release: ReleaseIdentityV1
    registry_commit: str = Field(pattern=_COMMIT)
    controller_commit: str = Field(pattern=_COMMIT)
    ci_receipt: ImmutableSourceReceiptV2
    artifacts: list[ArtifactSourceBindingV2] = Field(min_length=1, max_length=64)
    publisher: PublisherIdentityV2
    mode: Literal["dry-run", "publish"]
    request_digest: str = Field(pattern=_SHA256)

    @field_validator("artifacts")
    @classmethod
    def artifacts_have_unique_sources(
        cls, value: list[ArtifactSourceBindingV2]
    ) -> list[ArtifactSourceBindingV2]:
        if len({artifact.path for artifact in value}) != len(value):
            raise ValueError("artifact paths must be unique")
        if len({artifact.source_identity for artifact in value}) != len(value):
            raise ValueError("artifact source identities must be unique")
        return value

    def canonical_digest(self) -> str:
        """Return the SHA-256 of the canonical request without its self-binding field."""
        body = json.dumps(
            self.model_dump(mode="json", exclude={"request_digest"}),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(body).hexdigest()

    @model_validator(mode="after")
    def request_digest_matches_canonical_payload(self) -> PublisherRequestV2:
        if self.request_digest != self.canonical_digest():
            raise ValueError("request_digest must match the canonical request payload")
        return self


class PublisherRequestV3(_Contract):
    """V2 request facts plus one immutable canonical release-manifest binding."""

    schema_version: Literal["infralink.publisher-request.v3"]
    release: ReleaseIdentityV1
    registry_commit: str = Field(pattern=_COMMIT)
    controller_commit: str = Field(pattern=_COMMIT)
    ci_receipt: ImmutableSourceReceiptV2
    artifacts: list[ArtifactSourceBindingV2] = Field(min_length=1, max_length=64)
    publisher: PublisherIdentityV2
    mode: Literal["dry-run", "publish"]
    request_digest: str = Field(pattern=_SHA256)
    release_manifest: ReleaseManifestBindingV1

    @field_validator("artifacts")
    @classmethod
    def artifacts_have_unique_sources(
        cls, value: list[ArtifactSourceBindingV2]
    ) -> list[ArtifactSourceBindingV2]:
        if len({artifact.path for artifact in value}) != len(value):
            raise ValueError("artifact paths must be unique")
        if len({artifact.source_identity for artifact in value}) != len(value):
            raise ValueError("artifact source identities must be unique")
        return value

    def canonical_digest(self) -> str:
        """Return the SHA-256 of the canonical request without its self-binding field."""
        body = json.dumps(
            self.model_dump(mode="json", exclude={"request_digest"}),
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(body).hexdigest()

    @model_validator(mode="after")
    def request_digest_matches_canonical_payload(self) -> PublisherRequestV3:
        if self.request_digest != self.canonical_digest():
            raise ValueError("request_digest must match the canonical request payload")
        return self

    @model_validator(mode="after")
    def manifest_repository_matches_request_authority(self) -> PublisherRequestV3:
        if self.release_manifest.repository != self.ci_receipt.repository:
            raise ValueError("manifest repository must match request authority")
        if self.release_manifest.authority != ReleaseManifestAuthorityV1(
            release=self.release,
            registry_commit=self.registry_commit,
            controller_commit=self.controller_commit,
            ci_receipt=self.ci_receipt,
            artifacts=self.artifacts,
            publisher=self.publisher,
        ):
            raise ValueError("manifest authority must match request authority")
        return self


class ReleaseAttestationV2(_Contract):
    """Immutable publisher result bound to the exact canonical v2 request."""

    schema_version: Literal["infralink.release-attestation.v2"]
    request: PublisherRequestV2
    request_digest: str = Field(pattern=_SHA256)
    publisher_receipt: ImmutableSourceReceiptV2
    result: Literal["dry-run", "published"]
    tag: ReleaseTagV1 | None = None

    @model_validator(mode="after")
    def result_matches_request(self) -> ReleaseAttestationV2:
        if self.request_digest != self.request.canonical_digest():
            raise ValueError("request_digest must match the canonical publisher request")
        expected_result = "dry-run" if self.request.mode == "dry-run" else "published"
        if self.result != expected_result:
            raise ValueError("attestation result must match the requested mode")
        if self.result == "published":
            if self.tag is None:
                raise ValueError("published attestation requires a tag")
            if self.tag.name != self.request.release.identity:
                raise ValueError("tag name must match release identity")
        elif self.tag is not None:
            raise ValueError("dry-run attestation must not include a tag")
        return self


class ReleaseAttestationV3(_Contract):
    """Immutable publisher result bound to one canonical v3 request."""

    schema_version: Literal["infralink.release-attestation.v3"]
    request: PublisherRequestV3
    request_digest: str = Field(pattern=_SHA256)
    publisher_receipt: ImmutableSourceReceiptV2
    result: Literal["dry-run", "published"]
    tag: ReleaseTagV1 | None = None

    @model_validator(mode="after")
    def result_matches_request(self) -> ReleaseAttestationV3:
        if self.request_digest != self.request.canonical_digest():
            raise ValueError("request_digest must match the canonical publisher request")
        expected_result = "dry-run" if self.request.mode == "dry-run" else "published"
        if self.result != expected_result:
            raise ValueError("attestation result must match the requested mode")
        if self.result == "published":
            if self.tag is None:
                raise ValueError("published attestation requires a tag")
            if self.tag.name != self.request.release.identity:
                raise ValueError("tag name must match release identity")
        elif self.tag is not None:
            raise ValueError("dry-run attestation must not include a tag")
        return self


def parse_publisher_request_v2_json(document: str | bytes | bytearray) -> PublisherRequestV2:
    """Parse one publisher request through the strict v2 JSON boundary."""
    return PublisherRequestV2.model_validate(_load_strict_v2_json(document))


def parse_release_attestation_v2_json(document: str | bytes | bytearray) -> ReleaseAttestationV2:
    """Parse one publisher attestation through the strict v2 JSON boundary."""
    return ReleaseAttestationV2.model_validate(_load_strict_v2_json(document))


def parse_publisher_request_v3_json(document: str | bytes | bytearray) -> PublisherRequestV3:
    """Parse one publisher request through the strict v3 JSON boundary."""
    return PublisherRequestV3.model_validate(_load_strict_v2_json(document))


def parse_release_attestation_v3_json(document: str | bytes | bytearray) -> ReleaseAttestationV3:
    """Parse one publisher attestation through the strict v3 JSON boundary."""
    return ReleaseAttestationV3.model_validate(_load_strict_v2_json(document))
