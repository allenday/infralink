"""Read-only release-manager inspection boundary.

This command consumes the registry's versioned release-validation handoff and
local admission policy. It deliberately does not inspect Git, call CI, access
BWS, publish a tag, or mutate a host.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

import click
import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from infralink.cli.actions import action
from infralink.cli.contracts import (
    Binding,
    PublisherRequest,
    PublisherRequestResult,
    ReleaseAdmission,
    ReleaseArtifactBinding,
    ReleaseAttestation,
    ReleaseAttestationResult,
    ReleaseCandidate,
    ReleaseCandidateResult,
    ReleaseCiReceipt,
    ReleaseCompatibility,
    ReleaseFacts,
    ReleaseInspectResult,
    ReleaseProvenance,
    ReleasePublisher,
    ReleasePublisherReceipt,
    ReleaseSelection,
)
from infralink.cli.errors import CliFailure, ErrorCode, ExitCode
from infralink.cli.main import _context_for, _emit
from infralink.cli.output import ok_envelope

_IDENTITY = re.compile(r"^releases/([a-z0-9][a-z0-9-]{0,62})/([1-9][0-9]*)$")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class _ValidationRecord(_StrictModel):
    schema_version: Literal["infralink.release-validation.v1"]
    release_identity: str = Field(pattern=_IDENTITY.pattern)
    registry_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    annotated: Literal[True]
    status: Literal["active", "revoked"]


class _ChannelSelection(_StrictModel):
    mode: Literal["release-channel"]
    channel: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    recent_window: int = Field(ge=1, le=256)
    maximum_candidates: int = Field(ge=1, le=32)

    @model_validator(mode="after")
    def candidates_fit_window(self) -> _ChannelSelection:
        if self.maximum_candidates > self.recent_window:
            raise ValueError("maximum_candidates may not exceed recent_window")
        return self


class _RawRevisionSelection(_StrictModel):
    mode: Literal["raw-revision"]
    registry: _RegistryIdentity


class _RegistryIdentity(_StrictModel):
    remote: str
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")

    @field_validator("remote")
    @classmethod
    def credential_free_canonical_remote(cls, value: str) -> str:
        if not value or any(
            character.isspace() or unicodedata.category(character) == "Cc" for character in value
        ):
            raise ValueError("must be a credential-free canonical HTTPS or SSH Git remote")
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"https", "ssh"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not parsed.path.endswith(".git")
        ):
            raise ValueError("must be a credential-free canonical HTTPS or SSH Git remote")
        return value


class _Publisher(_StrictModel):
    state: Literal["unavailable", "eligible"]
    provider: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def provider_matches_state(self) -> _Publisher:
        if self.state == "eligible" and self.provider is None:
            raise ValueError("eligible publisher requires a provider identifier")
        return self


class _AdmissionDocument(_StrictModel):
    schema_version: Literal["infralink.release-admission.v1"]
    selection: _ChannelSelection | _RawRevisionSelection
    publisher: _Publisher = _Publisher(state="unavailable")


class _CiReceipt(_StrictModel):
    provider: str = Field(min_length=1, max_length=128)
    repository: str = Field(min_length=1, max_length=256)
    run: str = Field(min_length=1, max_length=128)


class _ArtifactBinding(_StrictModel):
    path: str = Field(min_length=1, max_length=256)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _CandidateDocument(_StrictModel):
    schema_version: Literal["infralink.release-candidate.v1"]
    release_identity: str = Field(pattern=_IDENTITY.pattern)
    registry_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    ci_receipt: _CiReceipt
    artifacts: list[_ArtifactBinding] = Field(min_length=1, max_length=64)
    consumers: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=64
    )


class _PublisherReceipt(_StrictModel):
    provider: str = Field(min_length=1, max_length=128)
    run: str = Field(min_length=1, max_length=128)


class _AttestationDocument(_StrictModel):
    schema_version: Literal["infralink.release-attestation.v1"]
    release_identity: str = Field(pattern=_IDENTITY.pattern)
    registry_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    controller_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    publisher_receipt: _PublisherReceipt
    tag: str = Field(pattern=_IDENTITY.pattern)
    consumers: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=64
    )

    @model_validator(mode="after")
    def tag_matches_release(self) -> _AttestationDocument:
        if self.tag != self.release_identity:
            raise ValueError("tag must match release identity")
        return self


def _load(path: Path, *, kind: str) -> object:
    try:
        body = path.read_text(encoding="utf-8")
        return json.loads(body) if path.suffix.casefold() == ".json" else yaml.safe_load(body)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise _invalid(kind, "could not be loaded", {"path": str(path)}) from error


def _invalid(kind: str, message: str, details: dict[str, Any]) -> CliFailure:
    code = {
        "validation": ErrorCode.RELEASE_VALIDATION_INVALID,
        "admission": ErrorCode.RELEASE_ADMISSION_REJECTED,
        "candidate": ErrorCode.RELEASE_CANDIDATE_INVALID,
        "attestation": ErrorCode.RELEASE_ATTESTATION_INVALID,
    }[kind]
    return CliFailure(
        code=code,
        message=f"Release {kind} {message}",
        exit_code=ExitCode.INPUT_ERROR,
        fix=(
            "Provide the published versioned release-validation record and bounded admission policy"
            if kind in {"validation", "admission"}
            else f"Provide a valid immutable release {kind} document"
        ),
        details=details,
        next_actions=[
            action(
                "help",
                ["infralink", "help", "release"],
                "Show release workflow inputs",
            )
        ],
    )


def _parse_inputs(
    validation_path: Path, admission_path: Path
) -> tuple[_ValidationRecord, _AdmissionDocument]:
    try:
        validation = _ValidationRecord.model_validate(_load(validation_path, kind="validation"))
    except (ValidationError, TypeError) as error:
        raise _invalid(
            "validation",
            "does not match infralink.release-validation.v1",
            {"path": str(validation_path)},
        ) from error
    try:
        admission = _AdmissionDocument.model_validate(_load(admission_path, kind="admission"))
    except (ValidationError, TypeError) as error:
        raise _invalid(
            "admission", "does not define a bounded selection", {"path": str(admission_path)}
        ) from error
    return validation, admission


def _parse_candidate(path: Path) -> _CandidateDocument:
    try:
        return _CandidateDocument.model_validate(_load(path, kind="candidate"))
    except (ValidationError, TypeError) as error:
        raise _invalid(
            "candidate",
            "does not match infralink.release-candidate.v1",
            {"path": str(path)},
        ) from error


def _parse_attestation(path: Path) -> _AttestationDocument:
    try:
        return _AttestationDocument.model_validate(_load(path, kind="attestation"))
    except (ValidationError, TypeError) as error:
        raise _invalid(
            "attestation",
            "does not match infralink.release-attestation.v1",
            {"path": str(path)},
        ) from error


def _admission_for(
    validation: _ValidationRecord, admission: _AdmissionDocument
) -> ReleaseAdmission:
    match = _IDENTITY.fullmatch(validation.release_identity)
    assert match is not None
    release_channel = match.group(1)
    selection = admission.selection
    if isinstance(selection, _ChannelSelection):
        if selection.channel != release_channel:
            raise _invalid(
                "admission",
                "does not admit this release channel",
                {"release_channel": release_channel, "admission_channel": selection.channel},
            )
        output = ReleaseSelection(
            mode=selection.mode,
            channel=selection.channel,
            recent_window=selection.recent_window,
            maximum_candidates=selection.maximum_candidates,
        )
    else:
        if selection.registry.commit != validation.registry_commit:
            raise _invalid(
                "admission",
                "does not admit this registry commit",
                {
                    "release_commit": validation.registry_commit,
                    "admission_commit": selection.registry.commit,
                },
            )
        output = ReleaseSelection(mode=selection.mode, registry_commit=selection.registry.commit)
    if validation.status == "revoked":
        return ReleaseAdmission(state="not-admitted", selection=output, reason="revoked")
    return ReleaseAdmission(state="admitted", selection=output)


def _publisher(value: _Publisher) -> ReleasePublisher:
    return ReleasePublisher(state=value.state, provider=value.provider)


def _candidate_output(candidate: _CandidateDocument) -> ReleaseCandidate:
    return ReleaseCandidate(
        identity=candidate.release_identity,
        registry_commit=candidate.registry_commit,
        controller_commit=candidate.controller_commit,
        ci_receipt=ReleaseCiReceipt(**candidate.ci_receipt.model_dump()),
        artifacts=[ReleaseArtifactBinding(**item.model_dump()) for item in candidate.artifacts],
        consumers=candidate.consumers,
    )


@click.group(name="release")
def release() -> None:
    """Inspect validated immutable registry releases."""


@release.command(name="inspect")
@click.option(
    "--release-validation",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
)
@click.option(
    "--admission", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
def inspect_release(release_validation: Path, admission: Path) -> None:
    """Inspect a versioned validation handoff against local bounded admission."""
    validation, admission_document = _parse_inputs(release_validation, admission)
    admitted = _admission_for(validation, admission_document)
    publisher = _publisher(admission_document.publisher)
    result = ReleaseInspectResult(
        release=ReleaseFacts(
            identity=validation.release_identity,
            registry_commit=validation.registry_commit,
            controller_commit=validation.controller_commit,
            annotated=validation.annotated,
            status=validation.status,
        ),
        admission=admitted,
        publisher=publisher,
        provenance=ReleaseProvenance(
            validation_schema_version=validation.schema_version,
            source="release-validation",
        ),
        compatibility=ReleaseCompatibility(
            selection_mode=admitted.selection.mode,
            controller_commit=validation.controller_commit,
        ),
    )
    actions = [
        action(
            "inspect",
            [
                "infralink",
                "release",
                "inspect",
                "--release-validation",
                str(release_validation),
                "--admission",
                str(admission),
            ],
            "Reinspect this immutable validation handoff",
        )
    ]
    _emit(ok_envelope(_context_for(path=["release", "inspect"]), result, actions))


@release.command(name="validate-candidate")
@click.option(
    "--candidate", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
def validate_candidate(candidate: Path) -> None:
    """Validate a local immutable release candidate without publishing it."""
    output = _candidate_output(_parse_candidate(candidate))
    actions = [
        action(
            "render-publisher-request",
            [
                "infralink",
                "release",
                "render-publisher-request",
                "--candidate",
                str(candidate),
                "--admission",
                "{admission}",
            ],
            "Render the explicit trusted-publisher handoff after selecting local admission policy",
            bindings={
                "admission": Binding(
                    type="string",
                    required=True,
                    source="local release admission policy path",
                )
            },
            safe=True,
        )
    ]
    _emit(
        ok_envelope(
            _context_for(path=["release", "validate-candidate"]),
            ReleaseCandidateResult(candidate=output),
            actions,
        )
    )


@release.command(name="render-publisher-request")
@click.option(
    "--candidate", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
@click.option(
    "--admission", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
def render_publisher_request(candidate: Path, admission: Path) -> None:
    """Render, but never invoke, an immutable trusted-publisher request."""
    candidate_document = _parse_candidate(candidate)
    try:
        admission_document = _AdmissionDocument.model_validate(_load(admission, kind="admission"))
    except (ValidationError, TypeError) as error:
        raise _invalid(
            "admission", "does not define a bounded selection", {"path": str(admission)}
        ) from error
    validation = _ValidationRecord(
        schema_version="infralink.release-validation.v1",
        release_identity=candidate_document.release_identity,
        registry_commit=candidate_document.registry_commit,
        controller_commit=candidate_document.controller_commit,
        annotated=True,
        status="active",
    )
    _admission_for(validation, admission_document)
    if admission_document.publisher.state != "eligible":
        raise CliFailure(
            code=ErrorCode.RELEASE_PUBLISHER_UNAVAILABLE,
            message="Trusted release publisher is unavailable",
            exit_code=ExitCode.NEGATIVE_RESULT,
            fix="Provision and activate the protected publisher tracked by infra-registry issue #251",
            next_actions=[
                action(
                    "publisher-prerequisites",
                    ["infralink", "help", "release", "render-publisher-request"],
                    "Review the immutable publisher request inputs after protected publisher activation",
                )
            ],
        )
    match = _IDENTITY.fullmatch(candidate_document.release_identity)
    assert match is not None
    request = PublisherRequest(
        schema_version="infralink.publisher-request.v1",
        release_identity=candidate_document.release_identity,
        channel=match.group(1),
        sequence=int(match.group(2)),
        registry_commit=candidate_document.registry_commit,
        controller_commit=candidate_document.controller_commit,
        ci_receipt=ReleaseCiReceipt(**candidate_document.ci_receipt.model_dump()),
        artifacts=[
            ReleaseArtifactBinding(**item.model_dump()) for item in candidate_document.artifacts
        ],
        consumers=candidate_document.consumers,
    )
    _emit(
        ok_envelope(
            _context_for(path=["release", "render-publisher-request"]),
            PublisherRequestResult(publisher_request=request),
            [
                action(
                    "inspect-attestation",
                    [
                        "infralink",
                        "release",
                        "inspect-attestation",
                        "--attestation",
                        "{attestation}",
                    ],
                    "Inspect the immutable publisher attestation after the trusted publisher completes",
                    bindings={
                        "attestation": Binding(
                            type="string",
                            required=True,
                            source="trusted publisher completion record path",
                        )
                    },
                )
            ],
        )
    )


@release.command(name="inspect-attestation")
@click.option(
    "--attestation", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True
)
def inspect_attestation(attestation: Path) -> None:
    """Inspect a publisher completion record without contacting a provider."""
    value = _parse_attestation(attestation)
    output = ReleaseAttestation(
        release_identity=value.release_identity,
        registry_commit=value.registry_commit,
        controller_commit=value.controller_commit,
        publisher_receipt=ReleasePublisherReceipt(**value.publisher_receipt.model_dump()),
        tag=value.tag,
        consumers=value.consumers,
    )
    _emit(
        ok_envelope(
            _context_for(path=["release", "inspect-attestation"]),
            ReleaseAttestationResult(attestation=output),
            [],
        )
    )
