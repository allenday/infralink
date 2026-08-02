#!/usr/bin/env python3
"""Build deterministic metadata for an Infralink release candidate."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import TypedDict

VERSION = "0.2.0"
WHEEL_NAME = f"infralink-{VERSION}-py3-none-any.whl"
SDIST_NAME = f"infralink-{VERSION}.tar.gz"
GENERATED_NAMES = {"manifest.json", "SHA256SUMS"}
COMMIT = re.compile(r"^[0-9a-f]{40}$")


class Artifact(TypedDict):
    name: str
    sha256: str


class ReleaseManifest(TypedDict):
    version: str
    source_commit: str
    workflow_run_id: str
    artifacts: list[Artifact]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as artifact:
        for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_artifacts(dist: Path) -> list[Path]:
    if not dist.is_dir():
        raise ValueError(f"distribution directory does not exist: {dist}")

    entries = sorted(
        (entry for entry in dist.iterdir() if entry.name not in GENERATED_NAMES),
        key=lambda entry: entry.name,
    )
    wheels = [entry for entry in entries if entry.name.endswith(".whl")]
    sdists = [entry for entry in entries if entry.name.endswith(".tar.gz")]
    if len(wheels) != 1:
        raise ValueError("distribution must contain exactly one wheel")
    if len(sdists) != 1:
        raise ValueError("distribution must contain exactly one sdist")

    expected = {WHEEL_NAME, SDIST_NAME}
    actual = {entry.name for entry in entries}
    unexpected = sorted(actual - expected)
    if unexpected:
        if any(name.startswith("infralink-") for name in unexpected):
            raise ValueError(f"artifact filename version does not match {VERSION}: {unexpected}")
        raise ValueError(f"unexpected artifact: {unexpected}")
    if actual != expected:
        raise ValueError(f"artifact filename version does not match {VERSION}")
    if any(not entry.is_file() or entry.is_symlink() for entry in entries):
        raise ValueError("release artifacts must be regular files")
    return entries


def build_manifest(
    dist: Path,
    *,
    source_commit: str,
    workflow_run_id: str,
) -> ReleaseManifest:
    """Return release metadata without consulting Git or environment state."""
    if COMMIT.fullmatch(source_commit) is None:
        raise ValueError("source commit must be exactly 40 lowercase hexadecimal characters")
    if not isinstance(workflow_run_id, str) or not workflow_run_id:
        raise ValueError("workflow run ID must be a non-empty string")

    artifacts = [
        Artifact(name=artifact.name, sha256=_sha256(artifact))
        for artifact in _release_artifacts(dist)
    ]
    return ReleaseManifest(
        version=VERSION,
        source_commit=source_commit,
        workflow_run_id=workflow_run_id,
        artifacts=artifacts,
    )


def write_release_metadata(
    dist: Path,
    *,
    source_commit: str,
    workflow_run_id: str,
    output: Path,
) -> ReleaseManifest:
    """Atomically replace canonical manifest and GNU checksum files."""
    manifest = build_manifest(
        dist,
        source_commit=source_commit,
        workflow_run_id=workflow_run_id,
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = (json.dumps(manifest, sort_keys=True, indent=2) + "\n").encode()
    checksum_bytes = "".join(
        f"{artifact['sha256']}  {artifact['name']}\n" for artifact in manifest["artifacts"]
    ).encode()

    manifest_tmp = output.with_name(f".{output.name}.tmp")
    checksums = dist / "SHA256SUMS"
    checksums_tmp = checksums.with_name(f".{checksums.name}.tmp")
    manifest_tmp.write_bytes(manifest_bytes)
    checksums_tmp.write_bytes(checksum_bytes)
    manifest_tmp.replace(output)
    checksums_tmp.replace(checksums)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--workflow-run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_release_metadata(
        args.dist,
        source_commit=args.source_commit,
        workflow_run_id=args.workflow_run_id,
        output=args.output,
    )


if __name__ == "__main__":
    main()
