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
