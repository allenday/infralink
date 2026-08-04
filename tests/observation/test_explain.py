from __future__ import annotations

import json

import pytest


def test_explain_catalog_covers_every_declared_emitted_code() -> None:
    from infralink.observation import DIAGNOSTIC_CODES, explain

    for code in DIAGNOSTIC_CODES:
        result = explain(code)
        assert result.code == code
        assert result.meaning
        assert result.affected_identity_types
        assert result.likely_causes
        assert result.next_actions


def test_unknown_explanation_is_typed_and_discoverable() -> None:
    from infralink.observation import DiagnosticCodeNotFoundError, explain

    with pytest.raises(DiagnosticCodeNotFoundError) as caught:
        explain("not-a-real-code")

    assert caught.value.code == "not-a-real-code"
    assert caught.value.available_codes
    assert "available_codes" in caught.value.suggestion


def test_explanations_never_contain_secret_values() -> None:
    from infralink.observation import DIAGNOSTIC_CODES, explain

    rendered = json.dumps([explain(code).to_dict() for code in DIAGNOSTIC_CODES])
    assert "secret-value" not in rendered
