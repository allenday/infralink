import shlex
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]
WOODPECKER = PROJECT_ROOT / ".woodpecker.yml"
GH_SHA256 = {
    "amd64": "62544b0f3759bbf1155c0ac3d75838b5fe23d66dfb75cf8368f84fff8f82b93e",
    "arm64": "a77f6d709c5100cda8e9bbb8d8b7143120121233d9102ba2f2bc254134db18dc",
}
COSIGN_SHA256 = {
    "amd64": "caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708",
    "arm64": "bd0f9763bca54de88699c3656ade2f39c9a1c7a2916ff35601caf23a79be0629",
}


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
    assert release["depends_on"] == ["quality-3.12"]
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
        "environment" not in steps[f"quality-{version}"] for version in ("3.12",)
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
        "scripts/release.py validate",
        'RELEASE_TAG="v$${RELEASE_VERSION:?}"',
        'git ls-remote --exit-code --tags origin "refs/tags/$${RELEASE_TAG}"',
        "repos/$${CI_REPO}/releases/tags/$${RELEASE_TAG}",
    ):
        assert required in text
    assert all(
        index < build_index
        for index, command in enumerate(commands)
        if "validate" in command or "ls-remote" in command or "releases/tags" in command
    )


def test_release_uses_checksum_pinned_tools_and_exact_assets() -> None:
    commands = "\n".join(load_woodpecker()["steps"]["release"]["commands"])
    release_surface = commands + (PROJECT_ROOT / "scripts" / "release.py").read_text()

    assert "scripts/release.py platform" in commands
    assert '--platform "$${CI_SYSTEM_PLATFORM}"' in commands
    assert ". /tmp/infralink-toolchain" in commands
    assert "GH_VERSION=2.76.2" in commands
    assert all(digest in release_surface for digest in GH_SHA256.values())
    assert "COSIGN_VERSION=2.4.3" in commands
    assert all(digest in release_surface for digest in COSIGN_SHA256.values())
    assert "gh_$${GH_VERSION}_linux_$${TOOL_ARCH}.tar.gz" in commands
    assert 'cosign-linux-$${TOOL_ARCH}"' in commands
    assert "linux_amd64.tar.gz" not in commands
    assert "cosign-linux-amd64" not in commands
    assert commands.count("sha256sum --check -") >= 2
    assert "python -m twine check" in commands
    assert "scripts/release.py checksums" in commands
    assert "--key env://COSIGN_PRIVATE_KEY" in commands
    assert "--bundle dist/SHA256SUMS.sigstore.json" in commands
    assert "COSIGN_PASSWORD=" in commands
    assert 'gh release create "$${RELEASE_TAG}"' in commands
    assert "docs/releases/$${RELEASE_TAG}.md" in commands
    assert "scripts/release.py assets --version" in commands
    publish = next(
        command
        for command in load_woodpecker()["steps"]["release"]["commands"]
        if "gh release create" in command
    )
    assert shlex.split(publish)[-1] == "$@"
