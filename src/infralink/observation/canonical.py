"""Canonical JSON serialization and SHA-256 identities for observation plans."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel
from pydantic_core import PydanticUndefined

_UNORDERED_LIST_KEYS = frozenset(
    {
        "applications",
        "datasource_bindings",
        "delivery_forms",
        "dependency_contracts",
        "endpoint_ids",
        "endpoint_overrides",
        "endpoints",
        "expected_statuses",
        "health",
        "health_signal_refs",
        "hosts",
        "logs",
        "metrics",
        "observation_backends",
        "operations_views",
        "provider_aliases",
        "readiness_suites",
        "renderer_binding_identities",
        "renderer_bindings",
        "required_dependency_edge_ids",
        "secret_binding_ids",
        "secret_bindings",
        "secret_slots",
        "service_instance_ids",
        "service_instances",
        "service_profiles",
        "signals",
        "waivers",
    }
)


def _canonical_text(item: object) -> str:
    return json.dumps(
        item, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
    )


def _scalar_tag(item: object) -> str:
    if item is None:
        return "null"
    if isinstance(item, bool):
        return "bool"
    if isinstance(item, int):
        return "int"
    if isinstance(item, float):
        return "float"
    if isinstance(item, str):
        return "str"
    return type(item).__name__


def _list_sort_key(item: object) -> tuple[str, str, str]:
    secondary = f"{_scalar_tag(item)}:{_canonical_text(item)}"
    if isinstance(item, Mapping):
        for key in ("id", "endpoint_id"):
            identity = item.get(key)
            if isinstance(identity, str):
                return key, identity, secondary
    if isinstance(item, (str, int, float, bool)):
        return "value", _scalar_tag(item), secondary
    return "canonical", secondary, secondary


def _sort_normalized_list(values: list[object]) -> list[object]:
    return sorted(values, key=_list_sort_key)


def _normalize(value: object, active: set[int]) -> object:
    if isinstance(value, BaseModel):
        identity = id(value)
        if identity in active:
            raise TypeError("canonical value contains an active-container cycle")
        active.add(identity)
        try:
            result: dict[str, object] = {}
            for key, field in type(value).model_fields.items():
                if key == "source_refs":
                    continue
                child = getattr(value, key)
                if child is None:
                    continue
                default = field.default
                if default is not PydanticUndefined and child == default:
                    continue
                field_default = field.get_default(call_default_factory=True, validated_data={})
                if field.default_factory is not None and child == field_default:
                    continue
                result[key] = _normalize(child, active)
            return result
        finally:
            active.remove(identity)
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical mappings require string keys")
        identity = id(value)
        if identity in active:
            raise TypeError("canonical value contains an active-container cycle")
        active.add(identity)
        try:
            result = {}
            for key, child in value.items():
                if child is None or key == "source_refs":
                    continue
                normalized = _normalize(child, active)
                if key in _UNORDERED_LIST_KEYS and isinstance(normalized, list):
                    normalized = _sort_normalized_list(normalized)
                result[key] = normalized
            return result
        finally:
            active.remove(identity)
    if isinstance(value, (list, tuple)):
        identity = id(value)
        if identity in active:
            raise TypeError("canonical value contains an active-container cycle")
        active.add(identity)
        try:
            return [_normalize(child, active) for child in value]
        finally:
            active.remove(identity)
    if isinstance(value, Enum):
        return _normalize(value.value, active)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical JSON does not support non-finite numbers")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: object) -> bytes:
    """Return compact UTF-8 JSON with sorted object keys and omitted null members."""

    return json.dumps(
        _normalize(value, set()),
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def canonical_digest(value: object) -> str:
    """Return the lowercase SHA-256 hex digest of canonical JSON."""

    return hashlib.sha256(canonical_json(value)).hexdigest()


canonical_json_bytes = canonical_json

__all__ = ["canonical_digest", "canonical_json", "canonical_json_bytes"]
