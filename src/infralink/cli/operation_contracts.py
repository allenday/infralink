"""Typed public result contracts for durable host apply operations."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from infralink.cli.contracts import ContractModel, DoctorTarget


class OperationSummary(ContractModel):
    id: str = Field(pattern=r"^woodpecker/[1-9][0-9]*/[1-9][0-9]*$")
    state: Literal["queued", "applying", "converged", "failed"]


class HostApplyResult(ContractModel):
    operation: OperationSummary | None = Field(default=None, exclude_if=lambda value: value is None)
    target: DoctorTarget
    dry_run: bool = Field(default=False, exclude_if=lambda value: not value)


class OperationStatusResult(ContractModel):
    operation: OperationSummary
    target: DoctorTarget | None = None
