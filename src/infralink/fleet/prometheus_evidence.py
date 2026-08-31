"""Versioned contract for private controller Prometheus evidence.

This module intentionally provides no Click command, Agent Surface operation,
network client, credential resolver, or artifact path selector. The private
controller produces this document; a later read-only fleet operation consumes
it through operator configuration.
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Literal

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "infralink.fleet-prometheus-evidence/v1"
CLOCK_SKEW_SECONDS = 60
_TARGET_ID_PATTERN = r"^[a-z][a-z0-9-]{0,127}$"
_REVISION_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_SIGNATURE_PATTERN = r"^[A-Za-z0-9+/]{86}==$"
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_DETAIL_CODES = {
    "observed": {"sample_observed"},
    "absent": {"sample_missing"},
    "query_error": {"provider_unavailable", "query_timeout", "query_failed"},
}

__all__ = [
    "FleetPrometheusEvidence",
    "FleetPrometheusEvidenceSignature",
    "FleetPrometheusTarget",
    "CLOCK_SKEW_SECONDS",
    "SCHEMA_VERSION",
]


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FleetPrometheusEvidenceSignature(_EvidenceModel):
    """Detached-value signature metadata retained inside signed evidence."""

    key_id: str = Field(pattern=_TARGET_ID_PATTERN)
    algorithm: Literal["ed25519"]
    value: str = Field(pattern=_SIGNATURE_PATTERN)

    @field_validator("value")
    @classmethod
    def _validate_ed25519_signature(cls, value: str) -> str:
        try:
            decoded = base64.b64decode(value, validate=True)
        except ValueError as error:
            raise ValueError("must be valid base64") from error
        if len(decoded) != 64:
            raise ValueError("must encode a 64-byte Ed25519 signature")
        return value


class FleetPrometheusTarget(_EvidenceModel):
    """One bounded, Registry-declared observation result."""

    status: Literal["observed", "absent", "query_error"]
    observed_at: str | None
    detail_code: Literal[
        "sample_observed",
        "sample_missing",
        "provider_unavailable",
        "query_timeout",
        "query_failed",
    ]

    @field_validator("observed_at")
    @classmethod
    def _require_utc_timestamp(cls, value: str | None) -> str | None:
        if value is None:
            return value
        _parse_utc_timestamp(value)
        return value

    @model_validator(mode="after")
    def _validate_status_detail_pair(self) -> FleetPrometheusTarget:
        if self.detail_code not in _DETAIL_CODES[self.status]:
            raise ValueError("detail_code is not valid for status")
        if self.status == "observed" and self.observed_at is None:
            raise ValueError("observed status requires observed_at")
        if self.status != "observed" and self.observed_at is not None:
            raise ValueError("non-observed status requires null observed_at")
        return self


class FleetPrometheusEvidence(_EvidenceModel):
    """The complete signed, bounded artifact produced by Infralink Ops."""

    schema_version: Literal["infralink.fleet-prometheus-evidence/v1"]
    registry_revision: str = Field(pattern=_REVISION_PATTERN)
    generated_at: str
    window_seconds: int = Field(ge=1, le=3600)
    max_age_seconds: int = Field(ge=1, le=3600)
    targets: dict[str, FleetPrometheusTarget] = Field(min_length=1, max_length=256)
    signature: FleetPrometheusEvidenceSignature

    @field_validator("generated_at")
    @classmethod
    def _validate_generated_at(cls, value: str) -> str:
        _parse_utc_timestamp(value)
        return value

    @model_validator(mode="after")
    def _validate_targets_within_window(self) -> FleetPrometheusEvidence:
        generated_at = _parse_utc_timestamp(self.generated_at)
        for target in self.targets.values():
            if target.observed_at is None:
                continue
            observed_at = _parse_utc_timestamp(target.observed_at)
            if observed_at > generated_at:
                raise ValueError("observed_at cannot be after generated_at")
            if (generated_at - observed_at).total_seconds() > self.window_seconds:
                raise ValueError("observed_at must fall within window_seconds")
        return self

    def is_fresh_at(self, now: datetime) -> bool:
        """Apply the v1 bounded clock-skew and signed maximum-age policy."""
        if now.tzinfo is None or now.utcoffset() != timezone.utc.utcoffset(now):
            raise ValueError("now must be UTC-aware")
        generated_at = _parse_utc_timestamp(self.generated_at)
        skew = timedelta(seconds=CLOCK_SKEW_SECONDS)
        return (
            generated_at - skew
            <= now
            <= generated_at + timedelta(seconds=self.max_age_seconds) + skew
        )

    def canonical_signed_bytes(self) -> bytes:
        """Return the exact payload the controller signs and reader verifies."""
        payload = self.model_dump(mode="json")
        signature = payload["signature"]
        assert isinstance(signature, dict)
        signature.pop("value")
        return json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")

    def verify_signature(self, public_key: Ed25519PublicKey) -> bool:
        """Verify an Ed25519 signature with a caller-selected trusted key."""
        try:
            public_key.verify(
                base64.b64decode(self.signature.value, validate=True), self.canonical_signed_bytes()
            )
        except InvalidSignature:
            return False
        return True


def _parse_utc_timestamp(value: str) -> datetime:
    """Accept one canonical UTC timestamp encoding, with whole-second precision."""
    if _TIMESTAMP_PATTERN.fullmatch(value) is None:
        raise ValueError("must be an RFC3339 UTC timestamp with whole-second Z precision")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as error:
        raise ValueError("must be a valid RFC3339 UTC timestamp") from error
    return parsed.replace(tzinfo=timezone.utc)
