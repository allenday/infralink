from __future__ import annotations

import subprocess
from pathlib import Path

from infralink.host_registry_state import inspect_host_manifest

HOST_ID = "32a3324f-c3d0-4a4f-9587-52c099bcb3fb"


def _checkout(tmp_path: Path) -> tuple[Path, Path]:
    worktree = tmp_path / "registry"
    hosts = worktree / "hosts"
    manifest = hosts / HOST_ID / "manifest.yml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("hosts: {}\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(worktree)], check=True)
    subprocess.run(
        ["git", "-C", str(worktree), "config", "user.email", "test@example.test"], check=True
    )
    subprocess.run(["git", "-C", str(worktree), "config", "user.name", "Test"], check=True)
    return hosts, manifest


def test_inspect_host_manifest_reports_untracked_generated_manifest(tmp_path: Path) -> None:
    hosts, manifest = _checkout(tmp_path)

    state = inspect_host_manifest(hosts, HOST_ID)

    assert state.state == "local_uncommitted"
    assert state.reason == "registry_manifest_untracked"
    assert state.manifest_path == manifest
    assert state.git_worktree == hosts.parent


def test_inspect_host_manifest_reports_clean_tracked_manifest(tmp_path: Path) -> None:
    hosts, manifest = _checkout(tmp_path)
    subprocess.run(
        ["git", "-C", str(hosts.parent), "add", str(manifest.relative_to(hosts.parent))], check=True
    )
    subprocess.run(["git", "-C", str(hosts.parent), "commit", "-qm", "track host"], check=True)

    state = inspect_host_manifest(hosts, HOST_ID)

    assert state.state == "tracked_clean"
    assert state.reason is None


def test_inspect_host_manifest_reports_local_manifest_modification(tmp_path: Path) -> None:
    hosts, manifest = _checkout(tmp_path)
    subprocess.run(
        ["git", "-C", str(hosts.parent), "add", str(manifest.relative_to(hosts.parent))], check=True
    )
    subprocess.run(["git", "-C", str(hosts.parent), "commit", "-qm", "track host"], check=True)
    manifest.write_text("hosts:\n  changed: true\n", encoding="utf-8")

    state = inspect_host_manifest(hosts, HOST_ID)

    assert state.state == "local_uncommitted"
    assert state.reason == "registry_manifest_modified"
