"""Canonical JSON serialization and SHA-256 identities for observation plans."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel

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


def _list_sort_key(item: object) -> tuple[str, str]:
    if isinstance(item, Mapping):
        for key in ("id", "endpoint_id"):
            identity = item.get(key)
            if isinstance(identity, str):
                return key, identity
    if isinstance(item, (str, int, float, bool)):
        return "value", str(item)
    return (
        "canonical",
        json.dumps(
            item, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        ),
    )


def _sort_normalized_list(values: list[object]) -> list[object]:
    return sorted(values, key=_list_sort_key)


def _normalize(value: object) -> object:
    if isinstance(value, BaseModel):
        value = value.model_dump(
            mode="json",
            exclude={"source_refs"},
            exclude_none=True,
            exclude_defaults=True,
        )
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("canonical mappings require string keys")
        result: dict[str, object] = {}
        for key, child in value.items():
            if child is None or key == "source_refs":
                continue
            normalized = _normalize(child)
            if key in _UNORDERED_LIST_KEYS and isinstance(normalized, list):
                normalized = _sort_normalized_list(normalized)
            result[key] = normalized
        return result
    if isinstance(value, (list, tuple)):
        return [_normalize(child) for child in value]
    if isinstance(value, Enum):
        return _normalize(value.value)
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
        _normalize(value),
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
