"""Typed public result contracts for durable host apply operations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from infralink.cli.contracts import ContractModel, DoctorTarget


class OperationSummary(ContractModel):
    id: str = Field(
        pattern=(
            r"^(?:op_[A-Za-z0-9_-]{8,128}|ssh/"
            r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}/"
            r"[0-9a-f]{32})$"
        )
    )
    state: Literal["queued", "applying", "converged", "failed"]


class OperationUnitFailure(ContractModel):
    active_state: str
    result: str
    exec_main_status: int


class OperationFailure(ContractModel):
    unit: OperationUnitFailure | None = None
    journal: list[str] = Field(default_factory=list, max_length=8)


class HostApplyResult(ContractModel):
    operation: OperationSummary | None = Field(default=None, exclude_if=lambda value: value is None)
    target: DoctorTarget
    dry_run: bool = Field(default=False, exclude_if=lambda value: not value)
    failure: OperationFailure | None = Field(default=None, exclude_if=lambda value: value is None)


class OperationStatusResult(ContractModel):
    operation: OperationSummary
    target: DoctorTarget | None = None
    failure: OperationFailure | None = Field(default=None, exclude_if=lambda value: value is None)


class AllowedSignerDiagnostic(ContractModel):
    """Public identity of the active Git SSH verification trust anchor."""

    principal: str = Field(pattern=r"^[A-Za-z0-9._-]{1,128}$")
    fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


VerifierUnavailableFact = Literal[
    "registry_remote",
    "registry_ref",
    "runtime_revision",
    "allowed_signer",
    "git_ssh_signature_capable",
    "fetched_tip",
    "signature_verification",
]


class HostVerifierDiagnostic(ContractModel):
    """Bounded, public-only facts used by the host-local V2 verifier."""

    registry_remote: str | None = Field(
        default=None, min_length=1, max_length=1024, exclude_if=lambda value: value is None
    )
    registry_ref: Literal["refs/heads/main"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    runtime_revision: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$", exclude_if=lambda value: value is None
    )
    allowed_signer: AllowedSignerDiagnostic | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    git_ssh_signature_capable: bool | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    fetched_tip: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{40}$", exclude_if=lambda value: value is None
    )
    signature_verification: Literal["passed", "failed", "unavailable"] | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    unavailable: list[VerifierUnavailableFact] = Field(
        default_factory=list, max_length=7, exclude_if=lambda value: not value
    )


class HostVerifierResult(ContractModel):
    target: DoctorTarget
    verifier: HostVerifierDiagnostic
