"""Strict, renderer-projected observer adapter bindings for Doctor."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from infralink.observation.models import StrictModel


class GatusAdapterBinding(StrictModel):
    """One Gatus result lookup with distinct declaration and API identities."""

    id: str = Field(min_length=1)
    renderer_kind: Literal["gatus"]
    observation_backend_id: str = Field(min_length=1)
    output_identity: str = Field(min_length=1)
    result_identity: str = Field(min_length=1)
    signal_ref: str = Field(min_length=1)

    @field_validator("result_identity")
    @classmethod
    def _require_nonblank_result_identity(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Gatus result identity must not be blank")
        return value


class AdapterBindings(StrictModel):
    """The complete strict binding projection consumed by Doctor."""

    schema_version: Literal["infra-observe.adapter-bindings.v2"]
    bindings: list[GatusAdapterBinding]

    @model_validator(mode="after")
    def _require_unique_identities(self) -> AdapterBindings:
        output_identities = [binding.output_identity for binding in self.bindings]
        result_identities = [binding.result_identity for binding in self.bindings]
        signal_refs = [binding.signal_ref for binding in self.bindings]
        if len(set(output_identities)) != len(output_identities):
            raise ValueError("Gatus output identities must be unique")
        if len(set(result_identities)) != len(result_identities):
            raise ValueError("Gatus result identities must be unique")
        if len(set(signal_refs)) != len(signal_refs):
            raise ValueError("Gatus signal references must be unique")
        return self

    @property
    def by_output_identity(self) -> dict[str, GatusAdapterBinding]:
        """Return the unique binding indexed by stable declaration identity."""

        return {binding.output_identity: binding for binding in self.bindings}

    @property
    def by_signal_ref(self) -> dict[str, GatusAdapterBinding]:
        """Return the unique binding indexed by the declared health signal."""

        return {binding.signal_ref: binding for binding in self.bindings}
