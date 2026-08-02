#!/usr/bin/env python3
"""Validate and assemble the fixed Infralink v0.2.0 release asset set."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path

VERSION = "0.2.0"
SHA_PATTERN = re.compile(r"[0-9a-f]{40}\Z", re.ASCII)
PACKAGE_ASSETS = (
    "infralink-0.2.0-py3-none-any.whl",
    "infralink-0.2.0.tar.gz",
)
RELEASE_ASSETS = (*PACKAGE_ASSETS, "SHA256SUMS", "SHA256SUMS.sigstore.json")
RELEASE_TOOLCHAINS = {
    "linux/amd64": (
        "amd64",
        "62544b0f3759bbf1155c0ac3d75838b5fe23d66dfb75cf8368f84fff8f82b93e",
        "caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708",
    ),
    "linux/arm64": (
        "arm64",
        "a77f6d709c5100cda8e9bbb8d8b7143120121233d9102ba2f2bc254134db18dc",
        "bd0f9763bca54de88699c3656ade2f39c9a1c7a2916ff35601caf23a79be0629",
    ),
}


class ReleaseError(ValueError):
    """The fixed release contract was not satisfied."""


def validate_release_inputs(
    *, requested_version: str, package_version: str, pipeline_sha: str, main_sha: str
) -> None:
    if requested_version != VERSION or package_version != VERSION:
        raise ReleaseError("release version does not match package version 0.2.0")
    if (
        SHA_PATTERN.fullmatch(pipeline_sha) is None
        or SHA_PATTERN.fullmatch(main_sha) is None
        or pipeline_sha != main_sha
    ):
        raise ReleaseError("pipeline commit is not the exact protected main commit")


def release_toolchain(platform: str) -> tuple[str, str, str]:
    try:
        return RELEASE_TOOLCHAINS[platform]
    except KeyError:
        raise ReleaseError(f"unsupported release platform: {platform}") from None


def write_toolchain_environment(platform: str, output: Path) -> None:
    arch, gh_sha256, cosign_sha256 = release_toolchain(platform)
    content = f"TOOL_ARCH={arch}\nGH_SHA256={gh_sha256}\nCOSIGN_SHA256={cosign_sha256}\n"
    try:
        output.write_text(content, encoding="ascii", newline="\n")
    except OSError:
        raise ReleaseError("cannot write release toolchain environment") from None


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        raise ReleaseError(f"cannot hash release asset: {path.name}") from None
    return digest.hexdigest()


def _files(root: Path) -> dict[str, Path]:
    try:
        return {path.name: path for path in root.iterdir() if path.is_file()}
    except OSError:
        raise ReleaseError("cannot inspect release asset directory") from None


def write_checksums(root: Path) -> Path:
    files = _files(root)
    if set(files) != set(PACKAGE_ASSETS):
        raise ReleaseError("invalid package asset set")
    output = root / "SHA256SUMS"
    content = "".join(f"{sha256(files[name])}  {name}\n" for name in PACKAGE_ASSETS)
    try:
        output.write_text(content, encoding="ascii", newline="\n")
    except OSError:
        raise ReleaseError("cannot write SHA256SUMS") from None
    return output


def release_assets(root: Path) -> list[Path]:
    files = _files(root)
    if set(files) != set(RELEASE_ASSETS):
        raise ReleaseError("invalid release asset set")
    return [files[name] for name in RELEASE_ASSETS]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("--requested-version", required=True)
    validate.add_argument("--package-version", required=True)
    validate.add_argument("--pipeline-sha", required=True)
    validate.add_argument("--main-sha", required=True)
    platform = commands.add_parser("platform")
    platform.add_argument("--platform", required=True)
    platform.add_argument("--output", required=True, type=Path)
    checksums = commands.add_parser("checksums")
    checksums.add_argument("--dist", required=True, type=Path)
    assets = commands.add_parser("assets")
    assets.add_argument("--dist", required=True, type=Path)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.command == "validate":
            validate_release_inputs(
                requested_version=args.requested_version,
                package_version=args.package_version,
                pipeline_sha=args.pipeline_sha,
                main_sha=args.main_sha,
            )
        elif args.command == "platform":
            write_toolchain_environment(args.platform, args.output)
        elif args.command == "checksums":
            write_checksums(args.dist)
        else:
            for asset in release_assets(args.dist):
                print(asset)
    except ReleaseError as exc:
        raise SystemExit(str(exc)) from None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
