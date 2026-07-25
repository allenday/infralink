import base64
import hashlib
import hmac
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from infralink.cli.errors import CliFailure, ErrorCode
from infralink.cli.pagination import (
    CursorCodec,
    load_cursor_key,
    page_items,
    production_cursor_codec,
)


def _signed_cursor(payload: object, key: bytes = b"test-only-key") -> str:
    encoded = base64.urlsafe_b64encode(
        payload
        if isinstance(payload, bytes)
        else json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).rstrip(b"=")
    signature = base64.urlsafe_b64encode(hmac.new(key, encoded, hashlib.sha256).digest()).rstrip(
        b"="
    )
    return f"{encoded.decode()}.{signature.decode()}"


def test_cursor_is_canonical_and_bound_to_command_collection_and_inputs() -> None:
    codec = CursorCodec(key=b"test-only-key")
    cursor = codec.encode(
        command="validate",
        collection="errors",
        offset=100,
        fingerprint="registry-sha",
    )

    encoded, _ = cursor.split(".")
    payload = json.loads(base64.urlsafe_b64decode(encoded + "=="))
    assert list(payload) == ["collection", "command", "fingerprint", "offset", "v"]
    assert codec.decode(cursor, "validate", "errors", "registry-sha") == 100

    for command, collection, fingerprint in [
        ("check", "errors", "registry-sha"),
        ("validate", "warnings", "registry-sha"),
        ("validate", "errors", "changed-registry-sha"),
    ]:
        with pytest.raises(CliFailure) as error:
            codec.decode(cursor, command, collection, fingerprint)
        assert error.value.code == ErrorCode.INVALID_CURSOR
        assert error.value.exit_code == 2


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "one-segment",
        "too.many.segments",
        "@@@.@@@",
        _signed_cursor(b"\xff"),
        _signed_cursor([]),
        _signed_cursor(
            {
                "v": True,
                "command": "hosts",
                "collection": "items",
                "offset": 1,
                "fingerprint": "sha",
            }
        ),
        _signed_cursor(
            {
                "v": 1,
                "command": "hosts",
                "collection": "items",
                "offset": 1,
                "fingerprint": "sha",
                "extra": True,
            }
        ),
    ],
)
def test_cursor_rejects_malformed_tokens_uniformly(cursor: str) -> None:
    codec = CursorCodec(key=b"test-only-key")

    with pytest.raises(CliFailure) as error:
        codec.decode(cursor, "hosts", "items", "sha")

    assert error.value.code == ErrorCode.INVALID_CURSOR
    assert error.value.message == "Cursor is invalid or no longer applicable"
    assert error.value.fix == "Restart the command without --cursor"
    assert error.value.details == {}


@pytest.mark.parametrize("offset", [True, 1.5, "1", -1])
def test_cursor_rejects_invalid_offsets(offset: object) -> None:
    cursor = _signed_cursor(
        {
            "v": 1,
            "command": "hosts",
            "collection": "items",
            "offset": offset,
            "fingerprint": "sha",
        }
    )

    with pytest.raises(CliFailure) as error:
        CursorCodec(key=b"test-only-key").decode(cursor, "hosts", "items", "sha")

    assert error.value.code == ErrorCode.INVALID_CURSOR


def test_cursor_rejects_tampering() -> None:
    codec = CursorCodec(key=b"test-only-key")
    cursor = codec.encode("hosts", "items", 1, "sha")
    payload, signature = cursor.split(".")
    tampered = f"{payload[:-1]}A.{signature}"

    with pytest.raises(CliFailure) as error:
        codec.decode(tampered, "hosts", "items", "sha")

    assert error.value.code == ErrorCode.INVALID_CURSOR


def test_cursor_rejects_signed_noncanonical_json() -> None:
    payload = b'{"v":1, "command":"hosts","collection":"items","offset":1,"fingerprint":"sha"}'
    cursor = _signed_cursor(payload)

    with pytest.raises(CliFailure) as error:
        CursorCodec(key=b"test-only-key").decode(cursor, "hosts", "items", "sha")

    assert error.value.code == ErrorCode.INVALID_CURSOR


def test_production_cursor_key_is_persisted_and_not_publicly_forgeable(
    tmp_path: Path,
) -> None:
    public_key = hashlib.sha256(b"infralink.cli/v1").digest()
    forged = CursorCodec(public_key).encode("hosts", "items", 1, "sha")
    codec = production_cursor_codec(state_dir=tmp_path)

    with pytest.raises(CliFailure):
        codec.decode(forged, "hosts", "items", "sha")

    cursor = codec.encode("hosts", "items", 1, "sha")
    assert production_cursor_codec(state_dir=tmp_path).decode(cursor, "hosts", "items", "sha") == 1
    key_path = tmp_path / "infralink/cursor.key"
    assert len(key_path.read_bytes()) == 32
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700


def test_production_cursor_key_is_stable_across_process_loads(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "XDG_STATE_HOME": str(tmp_path),
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    env.pop("INFRALINK_CURSOR_KEY", None)
    script = (
        "from infralink.cli.pagination import production_cursor_codec;"
        "print(production_cursor_codec().encode('hosts','items',1,'sha'))"
    )
    first = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout
    second = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    ).stdout
    assert first == second


def test_cursor_key_environment_override_accepts_only_32_base64url_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    key = bytes(range(32))
    encoded = base64.urlsafe_b64encode(key).rstrip(b"=").decode()
    monkeypatch.setenv("INFRALINK_CURSOR_KEY", encoded)
    assert load_cursor_key(state_dir=tmp_path) == key
    assert not (tmp_path / "infralink").exists()

    for invalid in [
        "not-base64!",
        base64.urlsafe_b64encode(b"short").decode(),
        base64.b64encode(b"\xff" * 32).decode(),
        f"{encoded}==",
    ]:
        monkeypatch.setenv("INFRALINK_CURSOR_KEY", invalid)
        with pytest.raises(CliFailure) as error:
            load_cursor_key(state_dir=tmp_path)
        assert error.value.code == ErrorCode.INVALID_CURSOR
        assert invalid not in str(error.value)


def test_cursor_key_rejects_insecure_permissions_and_symlinks(tmp_path: Path) -> None:
    key_dir = tmp_path / "infralink"
    key_dir.mkdir(mode=0o700)
    key_path = key_dir / "cursor.key"
    key_path.write_bytes(b"x" * 32)
    key_path.chmod(0o644)
    with pytest.raises(CliFailure):
        load_cursor_key(state_dir=tmp_path)

    key_path.unlink()
    target = tmp_path / "target"
    target.write_bytes(b"x" * 32)
    key_path.symlink_to(target)
    with pytest.raises(CliFailure):
        load_cursor_key(state_dir=tmp_path)


def test_cursor_key_rejects_insecure_state_directory(tmp_path: Path) -> None:
    key_dir = tmp_path / "infralink"
    key_dir.mkdir(mode=0o755)
    with pytest.raises(CliFailure):
        load_cursor_key(state_dir=tmp_path)


def test_cursor_key_permissions_ignore_restrictive_umask(tmp_path: Path) -> None:
    previous = os.umask(0o777)
    try:
        load_cursor_key(state_dir=tmp_path)
    finally:
        os.umask(previous)

    key_path = tmp_path / "infralink/cursor.key"
    assert stat.S_IMODE(key_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600


def test_cursor_key_failure_does_not_disclose_key_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    canary = "cursor-key-canary-material"
    monkeypatch.setenv("INFRALINK_CURSOR_KEY", canary)
    with pytest.raises(CliFailure) as error:
        load_cursor_key(state_dir=tmp_path)
    assert canary not in error.value.message
    assert canary not in error.value.fix
    assert canary not in json.dumps(error.value.details)


def test_page_items_never_exceeds_requested_limit() -> None:
    page = page_items(list(range(150)), limit=100, offset=0, next_cursor="next")
    assert page.items == list(range(100))
    assert page.page.limit == 100
    assert page.page.returned == 100
    assert page.page.total == 150
    assert page.page.next_cursor == "next"


def test_page_items_only_emits_cursor_when_more_items_exist() -> None:
    final = page_items(list(range(3)), limit=2, offset=2, next_cursor="unused")
    assert final.items == [2]
    assert final.page.next_cursor is None


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (1001, 0), (True, 0), (1, -1), (1, True)],
)
def test_page_items_rejects_invalid_bounds(limit: object, offset: object) -> None:
    with pytest.raises(CliFailure) as error:
        page_items([], limit=limit, offset=offset, next_cursor=None)  # type: ignore[arg-type]
    assert error.value.code == ErrorCode.INVALID_CURSOR
