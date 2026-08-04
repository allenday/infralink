from __future__ import annotations

import ast
import inspect
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

    assert {"invalid-as-of", "invalid-registry-revision"} <= set(DIAGNOSTIC_CODES)


def test_literal_emitted_codes_cannot_drift_from_explanation_catalog() -> None:
    from infralink.observation import DIAGNOSTIC_CODES, api, loader, planner

    emitted: set[str] = set()
    for module in (api, loader, planner):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if (
                    keyword.arg == "code"
                    and isinstance(keyword.value, ast.Constant)
                    and isinstance(keyword.value.value, str)
                ):
                    emitted.add(keyword.value.value)
            if (
                isinstance(node.func, ast.Name)
                and node.func.id in {"_add", "_finding", "_argument_diagnostic"}
                and len(node.args) >= 2
            ):
                code_arg = node.args[1] if node.func.id != "_argument_diagnostic" else node.args[0]
                if isinstance(code_arg, ast.Constant) and isinstance(code_arg.value, str):
                    emitted.add(code_arg.value)

    assert emitted <= set(DIAGNOSTIC_CODES)


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
