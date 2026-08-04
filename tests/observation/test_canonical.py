import math

import pytest

from infralink.observation.canonical import canonical_digest, canonical_json


def test_canonical_json_is_compact_utf8_and_sorts_mapping_keys() -> None:
    assert canonical_json({"z": None, "b": "café", "a": [2, 1]}) == (
        b'{"a":[2,1],"b":"caf\xc3\xa9"}'
    )


def test_canonical_digest_is_independent_of_mapping_order() -> None:
    assert canonical_digest({"b": 2, "a": 1}) == canonical_digest({"a": 1, "b": 2})


@pytest.mark.parametrize("value", [math.inf, -math.inf, math.nan, {1: "bad"}])
def test_canonical_json_rejects_values_outside_its_domain(value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical_json(value)
