"""Shared contract tests for controller-produced fleet evidence."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from jsonschema import Draft202012Validator, FormatChecker
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
    public_key = Ed25519PublicKey.from_public_bytes(
        base64.b64decode((FIXTURES / "public-key.base64").read_text(encoding="ascii"))
    )
    assert evidence.verify_signature(public_key) is True


def test_fixture_signature_matches_the_documented_private_test_vector() -> None:
    evidence = FleetPrometheusEvidence.model_validate(_fixture("valid.json"))
    private_key = Ed25519PrivateKey.from_private_bytes(bytes(range(32)))
    public_key = private_key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )

    assert (
        base64.b64encode(public_key).decode("ascii")
        == (FIXTURES / "public-key.base64").read_text(encoding="ascii").strip()
    )
    assert (
        base64.b64encode(private_key.sign(evidence.canonical_signed_bytes())).decode("ascii")
        == evidence.signature.value
    )


def test_target_status_and_detail_code_pairs_fail_closed() -> None:
    payload = _fixture("valid.json")
    payload["targets"]["controller-api"]["detail_code"] = "query_timeout"  # type: ignore[index]

    with pytest.raises(ValidationError):
        FleetPrometheusEvidence.model_validate(payload)


def test_observed_sample_outside_the_signed_window_fails_closed() -> None:
    payload = _fixture("valid.json")
    payload["targets"]["controller-api"]["observed_at"] = "2026-08-31T11:49:59Z"  # type: ignore[index]

    with pytest.raises(ValidationError):
        FleetPrometheusEvidence.model_validate(payload)


def test_json_schema_accepts_the_shared_fixture_and_rejects_extra_fields() -> None:
    validator = Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker()
    )
    assert list(validator.iter_errors(_fixture("valid.json"))) == []

    invalid = _fixture("valid.json")
    invalid["unexpected"] = True
    assert list(validator.iter_errors(invalid))


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("schema_version",), None),
        (("window_seconds",), "600"),
        (("generated_at",), "2026-08-31T12:00:00.1Z"),
    ],
)
def test_schema_and_model_reject_shared_invalid_shapes(
    path: tuple[str, ...], value: object
) -> None:
    payload = _fixture("valid.json")
    parent = payload
    for key in path[:-1]:
        parent = parent[key]  # type: ignore[index]
    if value is None:
        parent.pop(path[-1])
    else:
        parent[path[-1]] = value

    validator = Draft202012Validator(
        json.loads(SCHEMA.read_text(encoding="utf-8")), format_checker=FormatChecker()
    )
    assert list(validator.iter_errors(payload))
    with pytest.raises(ValidationError):
        FleetPrometheusEvidence.model_validate(payload)


def test_semantic_validator_rejects_nonportable_integer_and_invalid_calendar_timestamp() -> None:
    for path, value in (
        (("window_seconds",), 600.0),
        (("generated_at",), "2026-02-30T12:00:00Z"),
    ):
        payload = _fixture("valid.json")
        payload[path[0]] = value
        with pytest.raises(ValidationError):
            FleetPrometheusEvidence.model_validate(payload)


def test_signature_verification_fails_closed_for_tampering_and_wrong_key() -> None:
    evidence = FleetPrometheusEvidence.model_validate(_fixture("valid.json"))
    trusted_key = Ed25519PublicKey.from_public_bytes(
        base64.b64decode((FIXTURES / "public-key.base64").read_text(encoding="ascii"))
    )
    tampered = _fixture("valid.json")
    tampered["window_seconds"] = 599
    wrong_key = Ed25519PrivateKey.generate().public_key()

    assert FleetPrometheusEvidence.model_validate(tampered).verify_signature(trusted_key) is False
    assert evidence.verify_signature(wrong_key) is False


def test_signed_freshness_rule_has_exact_clock_skew_boundaries() -> None:
    evidence = FleetPrometheusEvidence.model_validate(_fixture("valid.json"))
    generated = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)

    assert evidence.is_fresh_at(generated - timedelta(seconds=60)) is True
    assert evidence.is_fresh_at(generated - timedelta(seconds=61)) is False
    assert evidence.is_fresh_at(generated + timedelta(seconds=960)) is True
    assert evidence.is_fresh_at(generated + timedelta(seconds=961)) is False


def test_contract_adds_no_click_or_agent_surface_command() -> None:
    source = (ROOT / "src" / "infralink" / "fleet" / "prometheus_evidence.py").read_text(
        encoding="utf-8"
    )

    assert "@click" not in source
    assert "@operator_surface" not in source
