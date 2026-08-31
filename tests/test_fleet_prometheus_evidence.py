"""Shared contract tests for controller-produced fleet evidence."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from infralink.fleet.prometheus_evidence import FleetPrometheusEvidence

ROOT = Path(__file__).parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "fleet-prometheus-evidence"
SCHEMA = ROOT / "src" / "infralink" / "schemas" / "fleet" / "prometheus-evidence-v1.json"


def _fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_valid_fixture_has_stable_unsigned_canonical_payload() -> None:
    evidence = FleetPrometheusEvidence.model_validate(_fixture("valid.json"))

    expected = (FIXTURES / "unsigned.json").read_text(encoding="utf-8").rstrip("\n").encode()
    assert evidence.canonical_signed_bytes() == expected


def test_target_status_and_detail_code_pairs_fail_closed() -> None:
    payload = _fixture("valid.json")
    payload["targets"][0]["detail_code"] = "query_timeout"  # type: ignore[index]

    with pytest.raises(ValidationError):
        FleetPrometheusEvidence.model_validate(payload)


def test_duplicate_target_ids_fail_closed() -> None:
    payload = _fixture("valid.json")
    payload["targets"][1]["id"] = "controller-api"  # type: ignore[index]

    with pytest.raises(ValidationError):
        FleetPrometheusEvidence.model_validate(payload)


def test_json_schema_accepts_the_shared_fixture_and_rejects_extra_fields() -> None:
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    assert list(validator.iter_errors(_fixture("valid.json"))) == []

    invalid = _fixture("valid.json")
    invalid["unexpected"] = True
    assert list(validator.iter_errors(invalid))


def test_contract_adds_no_click_or_agent_surface_command() -> None:
    source = (ROOT / "src" / "infralink" / "fleet" / "prometheus_evidence.py").read_text(
        encoding="utf-8"
    )

    assert "@click" not in source
    assert "@operator_surface" not in source
