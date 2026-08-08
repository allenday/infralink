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
from typing import Any, Literal
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
    ReleaseAdmission,
    ReleaseCompatibility,
    ReleaseFacts,
    ReleaseInspectResult,
    ReleaseProvenance,
    ReleasePublisher,
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


def _load(path: Path, *, kind: str) -> object:
    try:
        body = path.read_text(encoding="utf-8")
        return json.loads(body) if path.suffix.casefold() == ".json" else yaml.safe_load(body)
    except (OSError, json.JSONDecodeError, yaml.YAMLError) as error:
        raise _invalid(kind, "could not be loaded", {"path": str(path)}) from error


def _invalid(kind: str, message: str, details: dict[str, Any]) -> CliFailure:
    return CliFailure(
        code=ErrorCode.RELEASE_VALIDATION_INVALID
        if kind == "validation"
        else ErrorCode.RELEASE_ADMISSION_REJECTED,
        message=f"Release {kind} {message}",
        exit_code=ExitCode.INPUT_ERROR,
        fix="Provide the published versioned release-validation record and bounded admission policy",
        details=details,
        next_actions=[
            action(
                "help",
                ["infralink", "help", "release", "inspect"],
                "Show release inspection inputs",
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
