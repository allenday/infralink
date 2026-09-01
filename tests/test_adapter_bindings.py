from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from infralink.cli.adapter_bindings import AdapterBindings


def _binding(**overrides: object) -> dict[str, object]:
    return {
        "id": "gatus-reachability",
        "renderer_kind": "gatus",
        "observation_backend_id": "core-health",
        "output_identity": "dependency/reachability",
        "result_identity": "opaque-gatus-key-4bf09",
        "signal_ref": "dependency/reachability/health/reachable",
    } | overrides


def test_gatus_binding_keeps_stable_output_and_opaque_result_identities() -> None:
    bindings = AdapterBindings.model_validate(
        {
            "schema_version": "infra-observe.adapter-bindings.v2",
            "bindings": [_binding()],
        }
    )

    binding = bindings.by_output_identity["dependency/reachability"]
    assert binding.output_identity == "dependency/reachability"
    assert binding.result_identity == "opaque-gatus-key-4bf09"


@pytest.mark.parametrize(
    "bindings",
    [
        [{key: value for key, value in _binding().items() if key != "result_identity"}],
        [_binding(result_identity="")],
        [_binding(result_identity=" ")],
        [_binding(), _binding(id="gatus-second", output_identity="dependency/second")],
        [_binding(renderer_kind="not-gatus")],
        [_binding(unexpected="value")],
    ],
)
def test_gatus_binding_rejects_missing_empty_malformed_or_duplicate_result_identity(
    bindings: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        AdapterBindings.model_validate(
            {
                "schema_version": "infra-observe.adapter-bindings.v2",
                "bindings": bindings,
            }
        )


def test_gatus_binding_rejects_legacy_v1_without_a_fallback() -> None:
    with pytest.raises(ValidationError):
        AdapterBindings.model_validate(
            {
                "schema_version": "infra-observe.adapter-bindings.v1",
                "bindings": [_binding()],
            }
        )


def test_gatus_binding_rejects_duplicate_signal_ref() -> None:
    with pytest.raises(ValidationError, match="signal references must be unique"):
        AdapterBindings.model_validate(
            {
                "schema_version": "infra-observe.adapter-bindings.v2",
                "bindings": [
                    _binding(),
                    _binding(
                        id="gatus-second",
                        output_identity="dependency/second",
                        result_identity="opaque-gatus-key-second",
                    ),
                ],
            }
        )


def test_checked_in_adapter_bindings_schema_matches_generation_and_contract() -> None:
    from scripts.generate_adapter_binding_schemas import render_schemas

    expected = render_schemas()
    schema_path = (
        Path(__file__).parents[1] / "src/infralink/schemas/adapter-bindings/v2/document.json"
    )
    assert schema_path.read_bytes() == expected["v2/document.json"].encode("utf-8")

    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    valid = {"schema_version": "infra-observe.adapter-bindings.v2", "bindings": [_binding()]}
    assert validator.is_valid(valid)
    assert not validator.is_valid(
        {
            "schema_version": "infra-observe.adapter-bindings.v1",
            "bindings": [_binding()],
        }
    )
    assert not validator.is_valid(
        {
            "schema_version": "infra-observe.adapter-bindings.v2",
            "bindings": [
                {key: value for key, value in _binding().items() if key != "result_identity"}
            ],
        }
    )
