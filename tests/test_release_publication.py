import hashlib
import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "verify_release_publication.py"
SOURCE_SHA = "a" * 40
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


def load_module():
    spec = importlib.util.spec_from_file_location("verify_release_publication", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_assets(root: Path) -> dict[str, Path]:
    root.mkdir()
    result = {}
    for name in ASSET_NAMES:
        path = root / name
        path.write_bytes(f"bytes:{name}\n".encode())
        result[name] = path
    return result


def release_metadata(
    assets: dict[str, Path], *, draft: bool = True, names: tuple[str, ...] = ASSET_NAMES
) -> dict[str, object]:
    return {
        "tag_name": TAG,
        "name": TITLE,
        "body": "release notes\n",
        "target_commitish": SOURCE_SHA,
        "draft": draft,
        "prerelease": False,
        "assets": [
            {
                "id": index,
                "name": name,
                "size": assets[name].stat().st_size,
                "digest": "sha256:" + hashlib.sha256(assets[name].read_bytes()).hexdigest(),
                "state": "uploaded",
            }
            for index, name in enumerate(names, 1)
        ],
    }


def test_draft_release_plan_recovers_only_missing_expected_assets(tmp_path: Path) -> None:
    module = load_module()
    assets = write_assets(tmp_path / "assets")
    metadata = release_metadata(assets, names=ASSET_NAMES[:-2])

    plan = module.plan_release(
        metadata,
        source_sha=SOURCE_SHA,
        notes="release notes\n",
        asset_sources=assets,
    )

    assert plan == {
        "state": "recover_draft",
        "missing_assets": list(ASSET_NAMES[-2:]),
    }


def test_published_release_must_already_have_exact_asset_set(tmp_path: Path) -> None:
    module = load_module()
    assets = write_assets(tmp_path / "assets")
    metadata = release_metadata(assets, draft=False, names=ASSET_NAMES[:-1])

    with pytest.raises(module.ReleasePublicationError, match="published"):
        module.plan_release(
            metadata,
            source_sha=SOURCE_SHA,
            notes="release notes\n",
            asset_sources=assets,
        )


@pytest.mark.parametrize(
    ("draft", "state"),
    [(True, "recover_draft"), (False, "published")],
)
def test_complete_release_is_idempotent(tmp_path: Path, draft: bool, state: str) -> None:
    module = load_module()
    assets = write_assets(tmp_path / "assets")

    plan = module.plan_release(
        release_metadata(assets, draft=draft),
        source_sha=SOURCE_SHA,
        notes="release notes\n",
        asset_sources=assets,
    )

    assert plan == {"state": state, "missing_assets": []}


@pytest.mark.parametrize(
    "mutation",
    [
        lambda release: release.update(tag_name="v0.2.1"),
        lambda release: release.update(name="wrong"),
        lambda release: release.update(body="wrong notes"),
        lambda release: release.update(target_commitish="f" * 40),
        lambda release: release.update(prerelease=True),
        lambda release: release["assets"].append(dict(release["assets"][0])),
        lambda release: release["assets"][0].update(name="renamed"),
        lambda release: release["assets"][0].update(size=0),
        lambda release: release["assets"][0].update(digest="sha256:" + "f" * 64),
        lambda release: release["assets"][0].update(state="new"),
        lambda release: release["assets"].append(
            {
                "id": 99,
                "name": "extra",
                "size": 1,
                "digest": "sha256:" + "f" * 64,
                "state": "uploaded",
            }
        ),
    ],
)
def test_release_plan_rejects_wrong_identity_or_extra_assets(tmp_path: Path, mutation) -> None:
    module = load_module()
    assets = write_assets(tmp_path / "assets")
    metadata = release_metadata(assets)
    mutation(metadata)

    with pytest.raises(module.ReleasePublicationError):
        module.plan_release(
            metadata,
            source_sha=SOURCE_SHA,
            notes="release notes\n",
            asset_sources=assets,
        )


def test_verify_release_assets_requires_exact_downloaded_bytes(tmp_path: Path) -> None:
    module = load_module()
    assets = write_assets(tmp_path / "assets")
    downloaded = tmp_path / "downloaded"
    downloaded.mkdir()
    for name, source in assets.items():
        (downloaded / name).write_bytes(source.read_bytes())
    metadata = release_metadata(assets)

    module.verify_release_assets(
        metadata,
        notes="release notes\n",
        asset_sources=assets,
        downloaded_dir=downloaded,
        require_published=False,
        source_sha=SOURCE_SHA,
    )
    (downloaded / ASSET_NAMES[0]).write_bytes(b"substituted")
    with pytest.raises(module.ReleasePublicationError, match="digest"):
        module.verify_release_assets(
            metadata,
            notes="release notes\n",
            asset_sources=assets,
            downloaded_dir=downloaded,
            require_published=False,
            source_sha=SOURCE_SHA,
        )


@pytest.mark.parametrize("state", [None, "new", "processing", True, 1])
def test_release_asset_must_be_fully_uploaded(tmp_path: Path, state: object) -> None:
    module = load_module()
    assets = write_assets(tmp_path / "assets")
    metadata = release_metadata(assets)
    asset = metadata["assets"][0]
    if state is None:
        del asset["state"]
    else:
        asset["state"] = state

    with pytest.raises(module.ReleasePublicationError, match="assets"):
        module.plan_release(
            metadata,
            source_sha=SOURCE_SHA,
            notes="release notes\n",
            asset_sources=assets,
        )


@pytest.mark.parametrize("draft", [True, False])
def test_release_lookup_resolves_draft_or_published_database_id(draft: bool) -> None:
    module = load_module()
    assert module.release_database_id({"databaseId": 123, "isDraft": draft}) == 123


@pytest.mark.parametrize(
    "lookup",
    [
        {"databaseId": 0, "isDraft": True},
        {"databaseId": "123", "isDraft": True},
        {"databaseId": 123, "isDraft": "true"},
        {"databaseId": 123, "isDraft": True, "extra": False},
    ],
)
def test_release_lookup_rejects_ambiguous_metadata(lookup: dict[str, object]) -> None:
    module = load_module()
    with pytest.raises(module.ReleasePublicationError, match="lookup"):
        module.release_database_id(lookup)


def test_release_workflow_serializes_draft_recovery_and_verification() -> None:
    workflow = (PROJECT_ROOT / ".github" / "workflows" / "release.yml").read_text()
    assert "concurrency:" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "gh release create v0.2.0 --draft --verify-tag" in workflow
    assert "gh release view v0.2.0 --json databaseId,isDraft" in workflow
    assert "releases/$release_id" in workflow
    assert "releases/tags/v0.2.0" not in workflow
    assert "gh release upload v0.2.0" in workflow
    assert "Upload raced or failed; requiring exact refetched release state." in workflow
    assert workflow.count("gh release view v0.2.0 --json databaseId,isDraft") >= 2
    assert "gh release edit v0.2.0 --draft=false" in workflow
    assert "verify_release_publication.py" in workflow
    assert "--clobber" not in workflow
    assert "--force" not in workflow
