#!/usr/bin/env python3
"""Plan and verify fail-closed publication of an existing GitHub release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any

TAG = "v0.2.0"
TITLE = "Infralink v0.2.0"
ASSET_NAMES = (
    "SHA256SUMS",
    "infralink-0.2.0-py3-none-any.whl",
    "infralink-0.2.0.tar.gz",
    "manifest.json",
    "promotion.json",
    "woodpecker-evidence.json",
    "woodpecker-evidence.sigstore.json",
)
SHA_PATTERN = re.compile(r"[0-9a-f]{40}")
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


class ReleasePublicationError(Exception):
    """Raised when release state cannot be recovered without replacement."""


def _reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleasePublicationError
        result[key] = value
    return result


def load_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        if not path.is_file() or path.is_symlink() or path.stat().st_size > 1024 * 1024:
            raise ReleasePublicationError
        value = json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicates,
        )
    except (OSError, UnicodeError, ValueError, ReleasePublicationError):
        raise ReleasePublicationError(f"invalid {label}") from None
    if type(value) is not dict:
        raise ReleasePublicationError(f"invalid {label}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        if not path.is_file() or path.is_symlink():
            raise ReleasePublicationError("invalid release asset")
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise ReleasePublicationError("invalid release asset") from None
    return digest.hexdigest()


def asset_sources(root: Path) -> dict[str, Path]:
    try:
        paths = {path.name: path for path in root.iterdir()}
    except OSError:
        raise ReleasePublicationError("invalid release asset set") from None
    if set(paths) != set(ASSET_NAMES):
        raise ReleasePublicationError("invalid release asset set")
    for path in paths.values():
        _sha256(path)
    return paths


def _release_assets(
    release: dict[str, Any],
    asset_sources: dict[str, Path],
) -> dict[str, dict[str, Any]]:
    assets = release.get("assets")
    if type(assets) is not list:
        raise ReleasePublicationError("invalid release assets")
    result: dict[str, dict[str, Any]] = {}
    for asset in assets:
        if (
            type(asset) is not dict
            or type(asset.get("id")) is not int
            or asset["id"] <= 0
            or type(asset.get("name")) is not str
            or asset["name"] not in asset_sources
            or asset["name"] in result
            or type(asset.get("size")) is not int
            or type(asset.get("digest")) is not str
            or asset.get("state") != "uploaded"
        ):
            raise ReleasePublicationError("invalid release assets")
        source = asset_sources[asset["name"]]
        expected_digest = "sha256:" + _sha256(source)
        if (
            asset["size"] != source.stat().st_size
            or DIGEST_PATTERN.fullmatch(asset["digest"]) is None
            or asset["digest"] != expected_digest
        ):
            raise ReleasePublicationError("release asset digest mismatch")
        result[asset["name"]] = asset
    return result


def release_database_id(lookup: dict[str, Any]) -> int:
    database_id = lookup.get("databaseId")
    if (
        set(lookup) != {"databaseId", "isDraft"}
        or type(database_id) is not int
        or database_id <= 0
        or type(lookup.get("isDraft")) is not bool
    ):
        raise ReleasePublicationError("invalid release lookup")
    return database_id


def _validate_release_identity(release: dict[str, Any], *, notes: str, source_sha: str) -> bool:
    draft = release.get("draft")
    if (
        release.get("tag_name") != TAG
        or release.get("name") != TITLE
        or release.get("body") != notes
        or release.get("target_commitish") != source_sha
        or type(draft) is not bool
        or release.get("prerelease") is not False
    ):
        raise ReleasePublicationError("invalid release identity")
    return draft


def plan_release(
    release: dict[str, Any],
    *,
    source_sha: str,
    notes: str,
    asset_sources: dict[str, Path],
) -> dict[str, object]:
    if SHA_PATTERN.fullmatch(source_sha) is None or set(asset_sources) != set(ASSET_NAMES):
        raise ReleasePublicationError("invalid release expectations")
    draft = _validate_release_identity(release, notes=notes, source_sha=source_sha)
    existing = _release_assets(release, asset_sources)
    missing = [name for name in ASSET_NAMES if name not in existing]
    if not draft and missing:
        raise ReleasePublicationError("published release is incomplete")
    return {
        "state": "recover_draft" if draft else "published",
        "missing_assets": missing,
    }


def verify_release_assets(
    release: dict[str, Any],
    *,
    notes: str,
    asset_sources: dict[str, Path],
    downloaded_dir: Path,
    require_published: bool,
    source_sha: str,
) -> None:
    draft = _validate_release_identity(release, notes=notes, source_sha=source_sha)
    if require_published and draft:
        raise ReleasePublicationError("release remains draft")
    assets = _release_assets(release, asset_sources)
    if set(assets) != set(ASSET_NAMES):
        raise ReleasePublicationError("release asset set is incomplete")
    try:
        downloaded = {path.name: path for path in downloaded_dir.iterdir()}
    except OSError:
        raise ReleasePublicationError("invalid downloaded release assets") from None
    if set(downloaded) != set(ASSET_NAMES):
        raise ReleasePublicationError("invalid downloaded release assets")
    for name in ASSET_NAMES:
        if _sha256(downloaded[name]) != _sha256(asset_sources[name]):
            raise ReleasePublicationError("downloaded release asset digest mismatch")


def _notes(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ReleasePublicationError("invalid release notes") from None


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    plan = commands.add_parser("plan")
    plan.add_argument("--release-metadata", required=True, type=Path)
    plan.add_argument("--notes", required=True, type=Path)
    plan.add_argument("--asset-dir", required=True, type=Path)
    plan.add_argument("--source-sha", required=True)
    plan.add_argument("--output", required=True, type=Path)

    missing = commands.add_parser("missing")
    missing.add_argument("--plan", required=True, type=Path)
    missing.add_argument("--asset-dir", required=True, type=Path)

    downloads = commands.add_parser("downloads")
    downloads.add_argument("--release-metadata", required=True, type=Path)
    downloads.add_argument("--notes", required=True, type=Path)
    downloads.add_argument("--asset-dir", required=True, type=Path)
    downloads.add_argument("--source-sha", required=True)

    resolve = commands.add_parser("resolve")
    resolve.add_argument("--lookup", required=True, type=Path)

    verify = commands.add_parser("verify")
    verify.add_argument("--release-metadata", required=True, type=Path)
    verify.add_argument("--notes", required=True, type=Path)
    verify.add_argument("--asset-dir", required=True, type=Path)
    verify.add_argument("--downloaded-dir", required=True, type=Path)
    verify.add_argument("--source-sha", required=True)
    verify.add_argument("--require-published", action="store_true")
    return parser


def _run(args: argparse.Namespace) -> None:
    if args.command == "plan":
        plan = plan_release(
            load_json(args.release_metadata, label="release metadata"),
            source_sha=args.source_sha,
            notes=_notes(args.notes),
            asset_sources=asset_sources(args.asset_dir),
        )
        args.output.write_text(
            json.dumps(plan, separators=(",", ":"), sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return
    if args.command == "missing":
        plan = load_json(args.plan, label="release plan")
        missing_assets = plan.get("missing_assets")
        if (
            set(plan) != {"state", "missing_assets"}
            or plan.get("state") not in {"recover_draft", "published"}
            or type(missing_assets) is not list
        ):
            raise ReleasePublicationError("invalid release plan")
        missing_names: list[str] = []
        for name in missing_assets:
            if type(name) is not str or name not in ASSET_NAMES or name in missing_names:
                raise ReleasePublicationError("invalid release plan")
            missing_names.append(name)
        sources = asset_sources(args.asset_dir)
        for name in missing_names:
            print(sources[name])
        return
    if args.command == "downloads":
        release = load_json(args.release_metadata, label="release metadata")
        plan_release(
            release,
            source_sha=args.source_sha,
            notes=_notes(args.notes),
            asset_sources=asset_sources(args.asset_dir),
        )
        for name, asset in sorted(_release_assets(release, asset_sources(args.asset_dir)).items()):
            print(f"{asset['id']}\t{name}")
        return
    if args.command == "resolve":
        print(release_database_id(load_json(args.lookup, label="release lookup")))
        return
    if args.command == "verify":
        verify_release_assets(
            load_json(args.release_metadata, label="release metadata"),
            notes=_notes(args.notes),
            asset_sources=asset_sources(args.asset_dir),
            downloaded_dir=args.downloaded_dir,
            require_published=args.require_published,
            source_sha=args.source_sha,
        )
        return
    raise ReleasePublicationError("invalid command")


def main() -> int:
    try:
        _run(_parser().parse_args())
    except (OSError, ReleasePublicationError):
        return 4
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
