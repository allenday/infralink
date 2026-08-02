"""Bounded pages and signed opaque CLI cursors."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
from collections.abc import Sequence
from typing import TypeVar

from infralink.cli.contracts import Page, PageInfo
from infralink.cli.errors import CliFailure, ErrorCode

T = TypeVar("T")
_CURSOR_KEYS = {"v", "command", "collection", "offset", "fingerprint"}
_KEY_LENGTH = 32
_DEFAULT_KEY = hashlib.sha256(b"infralink.cli/v1").digest()


def invalid_cursor() -> CliFailure:
    """Return the uniform, non-sensitive invalid cursor failure."""
    return CliFailure(
        code=ErrorCode.INVALID_CURSOR,
        message="Cursor is invalid or no longer applicable",
        exit_code=2,
        fix="Restart the command without --cursor",
    )


def _encode_segment(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode_segment(value: str) -> bytes:
    if not value:
        raise ValueError("empty segment")
    padding = "=" * (-len(value) % 4)
    decoded = base64.b64decode(
        value + padding,
        altchars=b"-_",
        validate=True,
    )
    if _encode_segment(decoded) != value:
        raise ValueError("noncanonical segment")
    return decoded


def load_cursor_key() -> bytes:
    """Load an optional deployment key or the deterministic stateless key."""
    configured = os.environ.get("INFRALINK_CURSOR_KEY")
    if configured is None:
        return _DEFAULT_KEY
    try:
        key = _decode_segment(configured)
    except (ValueError, binascii.Error):
        raise invalid_cursor() from None
    if len(key) != _KEY_LENGTH or _encode_segment(key) != configured.rstrip("="):
        raise invalid_cursor()
    return key


def production_cursor_codec() -> CursorCodec:
    """Create the stateless production codec without filesystem side effects."""
    return CursorCodec(load_cursor_key())


class CursorCodec:
    """Detect corruption and bind untrusted continuation fields.

    The deterministic default token is not an authorization boundary and is
    forgeable by clients. Authorization must never depend on cursor contents.
    """

    def __init__(self, key: bytes) -> None:
        self._key = key

    def encode(
        self,
        command: str,
        collection: str,
        offset: int,
        fingerprint: str,
    ) -> str:
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise invalid_cursor()
        payload = {
            "v": 1,
            "command": command,
            "collection": collection,
            "offset": offset,
            "fingerprint": fingerprint,
        }
        encoded = _encode_segment(
            json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
        signature = hmac.new(self._key, encoded.encode("ascii"), hashlib.sha256).digest()
        return f"{encoded}.{_encode_segment(signature)}"

    def decode(
        self,
        cursor: str,
        command: str,
        collection: str,
        fingerprint: str,
    ) -> int:
        try:
            if not isinstance(cursor, str):
                raise ValueError("cursor is not text")
            encoded, encoded_signature = cursor.split(".")
            payload_bytes = _decode_segment(encoded)
            signature = _decode_segment(encoded_signature)
            expected = hmac.new(
                self._key,
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError("signature mismatch")
            payload = json.loads(payload_bytes.decode("utf-8"))
            if not isinstance(payload, dict) or set(payload) != _CURSOR_KEYS:
                raise ValueError("payload shape mismatch")
            canonical = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            if not hmac.compare_digest(payload_bytes, canonical):
                raise ValueError("payload is not canonical")
            offset = payload["offset"]
            if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
                raise ValueError("offset mismatch")
            if (
                isinstance(payload["v"], bool)
                or not isinstance(payload["v"], int)
                or payload["v"] != 1
                or payload["command"] != command
                or payload["collection"] != collection
                or payload["fingerprint"] != fingerprint
            ):
                raise ValueError("binding mismatch")
            return offset
        except (
            ValueError,
            TypeError,
            UnicodeError,
            json.JSONDecodeError,
            binascii.Error,
        ):
            raise invalid_cursor() from None


def page_items(
    items: Sequence[T],
    *,
    limit: int,
    offset: int,
    next_cursor: str | None,
) -> Page[T]:
    """Return one deterministic slice and retain a cursor only when more exists."""
    if (
        isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 1000
        or isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or offset > len(items)
    ):
        raise invalid_cursor()
    total = len(items)
    selected = list(items[offset : offset + limit])
    has_more = offset + len(selected) < total
    return Page[T](
        items=selected,
        page=PageInfo(
            limit=limit,
            returned=len(selected),
            total=total,
            next_cursor=next_cursor if has_more else None,
        ),
    )
