"""Tests for the provider-neutral secret boundary."""

from __future__ import annotations

import copy
import json
import pickle
from dataclasses import asdict, dataclass
from typing import get_type_hints

import pytest

from infralink.secrets import (
    SecretAudit,
    SecretReference,
    SecretResolver,
    SecretValue,
)

CANARY = "infralink-secret-canary-47291"
REDACTED = "[REDACTED]"


def assert_canary_absent(value: object) -> None:
    """Assert that common text views of a value do not expose the canary."""
    assert CANARY not in str(value)
    assert CANARY not in repr(value)


def test_secret_value_has_only_explicit_plaintext_access() -> None:
    secret = SecretValue(CANARY)

    assert str(secret) == REDACTED
    assert repr(secret) == "SecretValue([REDACTED])"
    assert secret.reveal() == CANARY
    assert not hasattr(secret, "value")


@pytest.mark.parametrize("format_spec", ["", "s", ">20"])
def test_secret_value_rejects_formatting(format_spec: str) -> None:
    secret = SecretValue(CANARY)

    with pytest.raises(TypeError) as exc_info:
        format(secret, format_spec)

    assert_canary_absent(exc_info.value)


def test_secret_value_rejects_json_serialization_without_leaking() -> None:
    secret = SecretValue(CANARY)

    with pytest.raises(TypeError) as exc_info:
        json.dumps({"secret": secret})

    assert_canary_absent(exc_info.value)


def test_secret_value_rejects_dataclass_conversion_without_leaking() -> None:
    @dataclass
    class Container:
        secret: SecretValue

    secret = SecretValue(CANARY)

    with pytest.raises(TypeError) as exc_info:
        asdict(Container(secret))

    assert_canary_absent(exc_info.value)


def test_secret_value_has_no_instance_dictionary() -> None:
    secret = SecretValue(CANARY)

    with pytest.raises(TypeError) as exc_info:
        vars(secret)

    assert_canary_absent(exc_info.value)


def test_secret_value_rejects_state_extraction_without_leaking() -> None:
    secret = SecretValue(CANARY)

    with pytest.raises(TypeError) as exc_info:
        secret.__getstate__()

    assert_canary_absent(exc_info.value)


def test_secret_value_is_redacted_in_exception_text() -> None:
    secret = SecretValue(CANARY)

    error = RuntimeError(secret)

    assert str(error) == REDACTED
    assert_canary_absent(error)


@pytest.mark.parametrize(
    "operation",
    [
        copy.copy,
        copy.deepcopy,
        pickle.dumps,
        bytes,
        iter,
    ],
)
def test_secret_value_rejects_unsafe_operations(operation: object) -> None:
    secret = SecretValue(CANARY)

    with pytest.raises(TypeError) as exc_info:
        operation(secret)  # type: ignore[operator]

    assert_canary_absent(exc_info.value)


def test_secret_value_uses_identity_equality_and_hashing() -> None:
    first = SecretValue(CANARY)
    second = SecretValue(CANARY)

    assert first == first
    assert first != second
    assert hash(first) == object.__hash__(first)


def test_secret_value_rejects_non_string_inputs_without_rendering_them() -> None:
    class Input:
        def __repr__(self) -> str:
            return CANARY

        def __str__(self) -> str:
            return CANARY

    value = Input()

    with pytest.raises(TypeError) as exc_info:
        SecretValue(value)  # type: ignore[arg-type]

    assert_canary_absent(exc_info.value)


def test_secret_reference_is_frozen_with_isolated_defaults() -> None:
    first = SecretReference(ref="db-password", project=None, locations=())
    second = SecretReference(ref="api-token", project="production", locations=("edge:a",))

    assert first.required is True
    assert second.required is True
    assert first.locations is not second.locations
    with pytest.raises(AttributeError):
        first.required = False  # type: ignore[misc]


def test_secret_audit_is_frozen_and_defaults_error_code() -> None:
    audit = SecretAudit(
        ref="db-password",
        project=None,
        present=True,
        accessible=True,
    )

    assert audit.error_code is None
    with pytest.raises(AttributeError):
        audit.present = False  # type: ignore[misc]


def test_secret_contract_annotations_are_typed() -> None:
    reference_hints = get_type_hints(SecretReference)
    audit_hints = get_type_hints(SecretAudit)

    assert reference_hints == {
        "ref": str,
        "project": str | None,
        "locations": tuple[str, ...],
        "required": bool,
    }
    assert audit_hints == {
        "ref": str,
        "project": str | None,
        "present": bool | None,
        "accessible": bool | None,
        "error_code": str | None,
    }


def test_secret_resolver_protocol_accepts_structural_implementation() -> None:
    class FakeResolver:
        def resolve(self, reference: SecretReference) -> SecretValue:
            return SecretValue(f"resolved:{reference.ref}")

        def audit(self, references: list[SecretReference]) -> list[SecretAudit]:
            return [
                SecretAudit(
                    ref=reference.ref,
                    project=reference.project,
                    present=True,
                    accessible=True,
                )
                for reference in references
            ]

    reference = SecretReference(ref="db-password", project=None, locations=())
    resolver: SecretResolver = FakeResolver()

    assert isinstance(resolver, SecretResolver)
    assert resolver.resolve(reference).reveal() == "resolved:db-password"
    assert resolver.audit([reference]) == [
        SecretAudit(
            ref="db-password",
            project=None,
            present=True,
            accessible=True,
        )
    ]
