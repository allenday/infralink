"""Local Git state inspection for directory-backed host declarations."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class HostManifestGitState:
    state: Literal["tracked_clean", "local_uncommitted", "unavailable"]
    reason: str | None
    manifest_path: Path
    git_worktree: Path


def inspect_host_manifest(registry_root: Path, host_id: str) -> HostManifestGitState:
    """Inspect only the local Git state of one generated host manifest."""
    git_worktree = registry_root.parent
    manifest_path = registry_root / host_id / "manifest.yml"
    if not manifest_path.is_file():
        return HostManifestGitState(
            "unavailable", "registry_manifest_missing", manifest_path, git_worktree
        )
    try:
        relative_path = manifest_path.relative_to(git_worktree)
        completed = subprocess.run(
            [
                "git",
                "-C",
                str(git_worktree),
                "status",
                "--porcelain",
                "--untracked-files=all",
                "--",
                str(relative_path),
            ],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return HostManifestGitState(
            "unavailable", "registry_git_unavailable", manifest_path, git_worktree
        )
    if completed.returncode != 0:
        return HostManifestGitState(
            "unavailable", "registry_git_unavailable", manifest_path, git_worktree
        )
    status = completed.stdout.strip()
    if not status:
        return HostManifestGitState("tracked_clean", None, manifest_path, git_worktree)
    reason = (
        "registry_manifest_untracked" if status.startswith("??") else "registry_manifest_modified"
    )
    return HostManifestGitState("local_uncommitted", reason, manifest_path, git_worktree)
