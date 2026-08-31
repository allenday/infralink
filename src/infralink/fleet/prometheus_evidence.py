"""Versioned contract for private controller Prometheus evidence.

This module intentionally provides no Click command, Agent Surface operation,
network client, credential resolver, or artifact path selector. The private
controller produces this document; a later read-only fleet operation consumes
it through operator configuration.
"""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "infralink.fleet-prometheus-evidence/v1"
_TARGET_ID_PATTERN = r"^[a-z][a-z0-9-]{0,127}$"
_REVISION_PATTERN = r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$"
_SIGNATURE_PATTERN = r"^[A-Za-z0-9+/]{86}==$"
_DETAIL_CODES = {
    "observed": {"sample_observed"},
    "absent": {"sample_missing"},
    "query_error": {"provider_unavailable", "query_timeout", "query_failed"},
}

__all__ = [
    "FleetPrometheusEvidence",
    "FleetPrometheusEvidenceSignature",
    "FleetPrometheusTarget",
    "SCHEMA_VERSION",
]


class _EvidenceModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


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

    id: str = Field(pattern=_TARGET_ID_PATTERN)
    status: Literal["observed", "absent", "query_error"]
    observed_at: datetime | None
    detail_code: Literal[
        "sample_observed",
        "sample_missing",
        "provider_unavailable",
        "query_timeout",
        "query_failed",
    ]

    @field_validator("observed_at", mode="before")
    @classmethod
    def _require_utc_timestamp(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("must be an RFC3339 UTC timestamp ending in Z")
        return value

    @field_validator("observed_at")
    @classmethod
    def _ensure_utc_offset(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.utcoffset() != timedelta(0):
            raise ValueError("must be a UTC timestamp")
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

    schema_version: Literal["infralink.fleet-prometheus-evidence/v1"] = (
        "infralink.fleet-prometheus-evidence/v1"
    )
    registry_revision: str = Field(pattern=_REVISION_PATTERN)
    generated_at: datetime
    window_seconds: int = Field(ge=1, le=3600)
    targets: tuple[FleetPrometheusTarget, ...] = Field(min_length=1, max_length=256)
    signature: FleetPrometheusEvidenceSignature

    @field_validator("generated_at", mode="before")
    @classmethod
    def _require_generated_at_utc(cls, value: object) -> object:
        if not isinstance(value, str) or not value.endswith("Z"):
            raise ValueError("must be an RFC3339 UTC timestamp ending in Z")
        return value

    @field_validator("generated_at")
    @classmethod
    def _ensure_generated_at_utc(cls, value: datetime) -> datetime:
        if value.utcoffset() != timedelta(0):
            raise ValueError("must be a UTC timestamp")
        return value

    @model_validator(mode="after")
    def _validate_unique_target_ids(self) -> FleetPrometheusEvidence:
        if len({target.id for target in self.targets}) != len(self.targets):
            raise ValueError("target IDs must be unique")
        return self

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
