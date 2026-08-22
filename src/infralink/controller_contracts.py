"""Public contracts between controller runtimes and environment adapters.

The runtime owns checkout, locking, invocation, and evidence persistence.  An
adapter owns environment-specific rendering and realization.  These models are
the narrow typed boundary between them.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ControllerAdapterContract(BaseModel):
    """A strict, versioned controller adapter document."""

    model_config = ConfigDict(extra="forbid")


class ControllerAdapterRequest(ControllerAdapterContract):
    """Explicit runtime inputs supplied to a controller adapter."""

    schema_version: Literal["infralink.controller-adapter-request/v1"] = (
        "infralink.controller-adapter-request/v1"
    )
    registry_root: str = Field(min_length=1, max_length=4096)
    registry_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    host_id: UUID
    runtime_root: str = Field(min_length=1, max_length=4096)
    services_root: str = Field(min_length=1, max_length=4096)
    phase: Literal["plan", "apply"]

    @field_validator("registry_root", "runtime_root", "services_root")
    @classmethod
    def require_absolute_path(cls, value: str) -> str:
        if not value.startswith("/"):
            raise ValueError("must be an absolute path")
        return value


AdapterActionCategory = Literal["render", "firewall", "artifact", "service", "observation"]
AdapterActionState = Literal["changed", "unchanged", "skipped", "failed"]
AdapterEvidenceStatus = Literal["passed", "failed", "unknown"]


class ControllerAdapterAction(ControllerAdapterContract):
    """A bounded summary of one adapter action category."""

    category: AdapterActionCategory
    state: AdapterActionState
    count: int = Field(ge=0, le=100_000)


class ControllerAdapterEvidence(ControllerAdapterContract):
    """A sanitized status fact emitted by an adapter."""

    kind: AdapterActionCategory
    status: AdapterEvidenceStatus


class ControllerAdapterResult(ControllerAdapterContract):
    """The typed result returned by a controller adapter invocation."""

    schema_version: Literal["infralink.controller-adapter-result/v1"] = (
        "infralink.controller-adapter-result/v1"
    )
    phase: Literal["plan", "apply"]
    status: Literal["planned", "applied", "failed"]
    registry_revision: str = Field(pattern=r"^[0-9a-f]{40}$")
    actions: list[ControllerAdapterAction] = Field(max_length=64)
    evidence: list[ControllerAdapterEvidence] = Field(max_length=64)

    @model_validator(mode="after")
    def require_status_for_phase(self) -> ControllerAdapterResult:
        if self.phase == "plan" and self.status not in {"planned", "failed"}:
            raise ValueError("plan results must be planned or failed")
        if self.phase == "apply" and self.status not in {"applied", "failed"}:
            raise ValueError("apply results must be applied or failed")
        return self
