"""Public contracts between the generic controller runtime and private adapters."""

from __future__ import annotations

from uuid import UUID

import pytest
from pydantic import ValidationError

from infralink.controller_contracts import (
    ControllerAdapterAction,
    ControllerAdapterEvidence,
    ControllerAdapterRequest,
    ControllerAdapterResult,
)


def test_controller_adapter_request_serializes_exact_runtime_inputs() -> None:
    request = ControllerAdapterRequest(
        registry_root="/var/lib/infralink/registry",
        registry_revision="a" * 40,
        host_id="32a3324f-c3d0-4a4f-9587-52c099bcb3fb",
        runtime_root="/opt/infra",
        services_root="/opt/services",
        phase="apply",
    )

    assert request.model_dump(mode="json") == {
        "schema_version": "infralink.controller-adapter-request/v1",
        "registry_root": "/var/lib/infralink/registry",
        "registry_revision": "a" * 40,
        "host_id": "32a3324f-c3d0-4a4f-9587-52c099bcb3fb",
        "runtime_root": "/opt/infra",
        "services_root": "/opt/services",
        "phase": "apply",
    }


def test_controller_adapter_request_rejects_noncanonical_runtime_state() -> None:
    with pytest.raises(ValidationError):
        ControllerAdapterRequest(
            registry_root="registry",
            registry_revision="main",
            host_id="not-a-uuid",
            runtime_root="infra",
            services_root="services",
            phase="apply",
        )


def test_controller_adapter_result_carries_bounded_sanitized_evidence() -> None:
    result = ControllerAdapterResult(
        phase="apply",
        status="applied",
        registry_revision="b" * 40,
        actions=[
            ControllerAdapterAction(category="render", state="changed", count=2),
            ControllerAdapterAction(category="service", state="unchanged", count=1),
        ],
        evidence=[
            ControllerAdapterEvidence(kind="render", status="passed"),
            ControllerAdapterEvidence(kind="service", status="passed"),
        ],
    )

    assert result.model_dump(mode="json") == {
        "schema_version": "infralink.controller-adapter-result/v1",
        "phase": "apply",
        "status": "applied",
        "registry_revision": "b" * 40,
        "actions": [
            {"category": "render", "state": "changed", "count": 2},
            {"category": "service", "state": "unchanged", "count": 1},
        ],
        "evidence": [
            {"kind": "render", "status": "passed"},
            {"kind": "service", "status": "passed"},
        ],
    }


def test_controller_adapter_result_rejects_unbounded_private_payloads() -> None:
    with pytest.raises(ValidationError):
        ControllerAdapterResult(
            phase="apply",
            status="applied",
            registry_revision="c" * 40,
            actions=[
                {
                    "category": "render",
                    "state": "changed",
                    "count": 1,
                    "private_secret": "must-not-cross-boundary",
                }
            ],
            evidence=[],
        )


@pytest.mark.parametrize(
    ("phase", "status"),
    [("plan", "applied"), ("apply", "planned")],
)
def test_controller_adapter_result_rejects_status_from_another_phase(
    phase: str, status: str
) -> None:
    with pytest.raises(ValidationError):
        ControllerAdapterResult(
            phase=phase,
            status=status,
            registry_revision="c" * 40,
            actions=[],
            evidence=[],
        )


def test_controller_adapter_request_exposes_uuid_type() -> None:
    request = ControllerAdapterRequest(
        registry_root="/var/lib/infralink/registry",
        registry_revision="d" * 40,
        host_id="32a3324f-c3d0-4a4f-9587-52c099bcb3fb",
        runtime_root="/opt/infra",
        services_root="/opt/services",
        phase="plan",
    )

    assert request.host_id == UUID("32a3324f-c3d0-4a4f-9587-52c099bcb3fb")
