"""Bounded pages and signed opaque CLI cursors."""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import TypeVar

from infralink.cli.contracts import Page, PageInfo
from infralink.cli.errors import CliFailure, ErrorCode

T = TypeVar("T")
_CURSOR_KEYS = {"v", "command", "collection", "offset", "fingerprint"}
_KEY_LENGTH = 32


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
    return base64.b64decode(
        value + padding,
        altchars=b"-_",
        validate=True,
    )


def _validate_key_file(file_descriptor: int) -> None:
    metadata = os.fstat(file_descriptor)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise invalid_cursor()


def _read_key(key_path: Path) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(key_path, flags)
    except OSError:
        raise invalid_cursor() from None
    try:
        _validate_key_file(file_descriptor)
        key = os.read(file_descriptor, _KEY_LENGTH + 1)
    except OSError:
        raise invalid_cursor() from None
    finally:
        os.close(file_descriptor)
    if len(key) != _KEY_LENGTH:
        raise invalid_cursor()
    return key


def _validate_key_directory(key_directory: Path) -> None:
    try:
        metadata = key_directory.lstat()
    except OSError:
        raise invalid_cursor() from None
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_ISLNK(metadata.st_mode)
        or metadata.st_uid != os.getuid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise invalid_cursor()


def _state_root(state_dir: Path | None) -> Path:
    if state_dir is not None:
        return state_dir
    configured = os.environ.get("XDG_STATE_HOME")
    if configured:
        return Path(configured)
    return Path.home() / ".local/state"


def load_cursor_key(*, state_dir: Path | None = None) -> bytes:
    """Load a validated environment key or a secure per-user state key."""
    configured = os.environ.get("INFRALINK_CURSOR_KEY")
    if configured is not None:
        try:
            key = _decode_segment(configured)
        except (ValueError, binascii.Error):
            raise invalid_cursor() from None
        if len(key) != _KEY_LENGTH or _encode_segment(key) != configured.rstrip("="):
            raise invalid_cursor()
        return key

    key_directory = _state_root(state_dir) / "infralink"
    try:
        key_directory.mkdir(mode=0o700, parents=True, exist_ok=False)
        key_directory.chmod(0o700)
    except FileExistsError:
        pass
    except OSError:
        raise invalid_cursor() from None
    _validate_key_directory(key_directory)
    key_path = key_directory / "cursor.key"
    try:
        return _read_key(key_path)
    except CliFailure:
        try:
            exists = key_path.lstat()
        except FileNotFoundError:
            exists = None
        except OSError:
            raise invalid_cursor() from None
        if exists is not None:
            raise

    key = secrets.token_bytes(_KEY_LENGTH)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        file_descriptor = os.open(key_path, flags, 0o600)
    except FileExistsError:
        return _read_key(key_path)
    except OSError:
        raise invalid_cursor() from None
    try:
        os.fchmod(file_descriptor, 0o600)
        _validate_key_file(file_descriptor)
        view = memoryview(key)
        while view:
            written = os.write(file_descriptor, view)
            if written <= 0:
                raise OSError("cursor key write failed")
            view = view[written:]
        os.fsync(file_descriptor)
    except (OSError, CliFailure):
        raise invalid_cursor() from None
    finally:
        os.close(file_descriptor)
    return key


def production_cursor_codec(*, state_dir: Path | None = None) -> CursorCodec:
    """Create a codec from production key configuration."""
    return CursorCodec(load_cursor_key(state_dir=state_dir))


class CursorCodec:
    """Encode and verify cursor payloads bound to a query snapshot."""

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
