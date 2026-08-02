import shlex
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WOODPECKER = PROJECT_ROOT / ".woodpecker.yml"
GH_SHA256 = "62544b0f3759bbf1155c0ac3d75838b5fe23d66dfb75cf8368f84fff8f82b93e"
COSIGN_SHA256 = "caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708"


def load_woodpecker() -> dict[str, object]:
    return yaml.safe_load(WOODPECKER.read_text(encoding="utf-8"))


def test_exactly_one_release_step_is_manual_main_after_all_quality_steps() -> None:
    workflow = load_woodpecker()
    steps = workflow["steps"]
    release_steps = [
        step
        for step in steps.values()
        if "gh release create" in "\n".join(step.get("commands", []))
    ]

    assert len(release_steps) == 1
    release = release_steps[0]
    assert release["image"] == "python:3.12-slim-bookworm"
    assert release["depends_on"] == ["quality-3.10", "quality-3.11", "quality-3.12"]
    assert release["when"] == [
        {
            "event": "manual",
            "branch": "main",
        }
    ]


def test_release_secrets_are_isolated_to_manual_release_step() -> None:
    workflow = load_woodpecker()
    steps = workflow["steps"]
    release = steps["release"]

    assert release["environment"] == {
        "GH_TOKEN": {"from_secret": "infralink_release_github_token"},
        "COSIGN_PRIVATE_KEY": {"from_secret": "infralink_gate_cosign_private_key"},
    }
    assert all(
        "environment" not in steps[f"quality-{version}"] for version in ("3.10", "3.11", "3.12")
    )
    assert WOODPECKER.read_text().count("infralink_release_github_token") == 1
    assert WOODPECKER.read_text().count("infralink_gate_cosign_private_key") == 1


def test_release_validates_identity_and_absent_remote_state_before_building() -> None:
    release = load_woodpecker()["steps"]["release"]
    commands = release["commands"]
    text = "\n".join(commands)
    build_index = next(
        index for index, command in enumerate(commands) if "python -m build" in command
    )

    for required in (
        "$${RELEASE_VERSION:?}",
        "$${CI_COMMIT_SHA}",
        "git fetch --no-tags origin main",
        "git rev-parse FETCH_HEAD",
        "scripts/release_v0_2.py validate",
        "git ls-remote --exit-code --tags origin refs/tags/v0.2.0",
        "repos/$${CI_REPO}/releases/tags/v0.2.0",
    ):
        assert required in text
    assert all(
        index < build_index
        for index, command in enumerate(commands)
        if "validate" in command or "ls-remote" in command or "releases/tags" in command
    )


def test_release_uses_checksum_pinned_tools_and_exact_assets() -> None:
    commands = "\n".join(load_woodpecker()["steps"]["release"]["commands"])

    assert "GH_VERSION=2.76.2" in commands
    assert GH_SHA256 in commands
    assert "COSIGN_VERSION=2.4.3" in commands
    assert COSIGN_SHA256 in commands
    assert commands.count("sha256sum --check -") >= 2
    assert "python -m twine check" in commands
    assert "scripts/release_v0_2.py checksums" in commands
    assert "--key env://COSIGN_PRIVATE_KEY" in commands
    assert "--bundle dist/SHA256SUMS.sigstore.json" in commands
    assert "COSIGN_PASSWORD=" in commands
    assert "gh release create v0.2.0" in commands
    assets = (
        "dist/infralink-0.2.0-py3-none-any.whl",
        "dist/infralink-0.2.0.tar.gz",
        "dist/SHA256SUMS",
        "dist/SHA256SUMS.sigstore.json",
    )
    publish = next(
        command
        for command in load_woodpecker()["steps"]["release"]["commands"]
        if "gh release create" in command
    )
    assert tuple(shlex.split(publish)[-4:]) == assets
