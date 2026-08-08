"""Typed inputs emitted by registry CI around the trusted publisher boundary."""

from __future__ import annotations

from pathlib import PurePath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_CHANNEL = r"^[a-z0-9][a-z0-9-]{0,62}$"
_IDENTITY = r"^releases/[a-z0-9][a-z0-9-]{0,62}/[1-9][0-9]*$"
_COMMIT = r"^[0-9a-f]{40}$"
_SHA256 = r"^[0-9a-f]{64}$"


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
