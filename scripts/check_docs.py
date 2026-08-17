#!/usr/bin/env python3
"""Validate repository Markdown links without third-party dependencies."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

LINK_RE = re.compile(r"(?<!!)\[[^\]\n]+\]\(([^)\n]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
SKIP_SCHEMES = {"http", "https", "mailto", "tel"}


@dataclass(frozen=True)
class LinkError:
    source: Path
    line: int
    target: str
    message: str

    def render(self, root: Path) -> str:
        source = self.source.relative_to(root)
        return f"{source}:{self.line}: {self.target}: {self.message}"


def _git_files(root: Path, *args: str) -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files", "-z", *args],
        cwd=root,
        check=True,
        capture_output=True,
        text=False,
    )
    names = [name for name in proc.stdout.decode("utf-8").split("\0") if name]
    return [root / name for name in names]


def markdown_files(root: Path) -> list[Path]:
    tracked = _git_files(root, "*.md")
    untracked = _git_files(root, "--others", "--exclude-standard", "*.md")
    return sorted({*tracked, *untracked})


def _strip_title(raw: str) -> str:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        return target[1 : target.index(">")]
    match = re.match(r"(\S+)(?:\s+['\"].*['\"])?$", target)
    return match.group(1) if match else target


def _is_external(target: str) -> bool:
    scheme = urlsplit(target).scheme.lower()
    return scheme in SKIP_SCHEMES


def _split_fragment(target: str) -> tuple[str, str]:
    path, marker, fragment = target.partition("#")
    return unquote(path), unquote(fragment) if marker else ""


def slugify_heading(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip().lower()
    text = re.sub(r"[`*_~\[\]()<>{}:;,.!?\"']", "", text)
    text = re.sub(r"[^a-z0-9 -]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text.strip("-")


def heading_slugs(path: Path) -> set[str]:
    slugs: set[str] = set()
    seen: dict[str, int] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = HEADING_RE.match(line)
        if not match:
            continue
        base = slugify_heading(match.group(2))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        slugs.add(base if count == 0 else f"{base}-{count}")
    return slugs


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def check_file(root: Path, source: Path) -> list[LinkError]:
    text = source.read_text(encoding="utf-8")
    errors: list[LinkError] = []
    for match in LINK_RE.finditer(text):
        raw_target = _strip_title(match.group(1))
        if not raw_target or _is_external(raw_target):
            continue
        path_text, fragment = _split_fragment(raw_target)
        if urlsplit(path_text).scheme:
            continue
        target_path = source if not path_text else (source.parent / path_text)
        try:
            resolved = target_path.resolve(strict=False)
            resolved.relative_to(root.resolve())
        except ValueError:
            errors.append(
                LinkError(
                    source, _line_number(text, match.start()), raw_target, "escapes repo root"
                )
            )
            continue
        if path_text:
            if raw_target.endswith("/") or target_path.is_dir():
                if not target_path.is_dir():
                    errors.append(
                        LinkError(
                            source,
                            _line_number(text, match.start()),
                            raw_target,
                            "directory does not exist",
                        )
                    )
                continue
            if not target_path.is_file():
                errors.append(
                    LinkError(
                        source,
                        _line_number(text, match.start()),
                        raw_target,
                        "file does not exist",
                    )
                )
                continue
        if fragment and fragment not in heading_slugs(target_path):
            errors.append(
                LinkError(source, _line_number(text, match.start()), raw_target, "anchor not found")
            )
    return errors


def check_markdown_links(root: Path, files: list[Path] | None = None) -> list[LinkError]:
    root = root.resolve()
    targets = files if files is not None else markdown_files(root)
    errors: list[LinkError] = []
    for path in targets:
        errors.extend(check_file(root, path.resolve()))
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    root = args.root.resolve()
    errors = check_markdown_links(root)
    for error in errors:
        print(error.render(root), file=sys.stderr)
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
