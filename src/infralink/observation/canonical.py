"""Canonical JSON serialization and SHA-256 identities for observation plans."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from enum import Enum

from pydantic import BaseModel


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
        return {
            key: _normalize(child)
            for key, child in value.items()
            if child is not None and key != "source_refs"
        }
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
