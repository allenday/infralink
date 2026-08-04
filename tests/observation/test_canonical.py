import math

import pytest
from pydantic import BaseModel

from infralink.observation.canonical import canonical_digest, canonical_json
from infralink.observation.loader import canonical_parsed_content


def test_canonical_json_is_compact_utf8_and_sorts_mapping_keys() -> None:
    assert canonical_json({"z": None, "b": "café", "a": [2, 1]}) == (
        b'{"a":[2,1],"b":"caf\xc3\xa9"}'
    )


def test_canonical_digest_is_independent_of_mapping_order() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})


def test_semantically_unordered_source_lists_use_declared_sort_keys() -> None:
    first = {"hosts": [{"id": "b"}, {"id": "a"}]}
    second = {"hosts": [{"id": "a"}, {"id": "b"}]}
    assert canonical_parsed_content(first) == canonical_parsed_content(second)


def test_author_ordered_view_and_suite_members_are_not_sorted() -> None:
    first = {"sections": [{"id": "b"}, {"id": "a"}], "members": [{"id": "b"}, {"id": "a"}]}
    second = {"sections": [{"id": "a"}, {"id": "b"}], "members": [{"id": "a"}, {"id": "b"}]}
    assert canonical_json(first) != canonical_json(second)


def test_duplicate_primary_id_uses_canonical_content_as_secondary_key() -> None:
    records = [{"id": "same", "value": 2}, {"id": "same", "value": 1}]
    assert canonical_json({"hosts": records}) == canonical_json({"hosts": records[::-1]})


def test_unordered_scalar_keys_are_type_tagged_and_deterministic() -> None:
    values: list[object] = [1, "1", False, 1.0]
    assert canonical_json({"endpoint_ids": values}) == canonical_json(
        {"endpoint_ids": values[::-1]}
    )


@pytest.mark.parametrize("kind", ["mapping", "list"])
def test_canonical_json_rejects_active_container_cycles(kind: str) -> None:
    value: object
    if kind == "mapping":
        mapping: dict[str, object] = {}
        mapping["self"] = mapping
        value = mapping
    else:
        sequence: list[object] = []
        sequence.append(sequence)
        value = sequence
    with pytest.raises(TypeError, match="cycle"):
        canonical_json(value)


def test_canonical_json_allows_repeated_noncyclic_shared_objects() -> None:
    shared = {"value": 1}
    assert canonical_json([shared, shared]) == b'[{"value":1},{"value":1}]'


def test_canonical_json_rejects_active_model_cycle() -> None:
    class Node(BaseModel):
        child: object | None = None

    node = Node()
    object.__setattr__(node, "child", node)
    with pytest.raises(TypeError, match="cycle"):
        canonical_json(node)


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, {1: "bad"}])
def test_canonical_json_rejects_values_outside_its_domain(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json(value)
