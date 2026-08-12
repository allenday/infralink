import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "release.py"
SOURCE_SHA = "a" * 40
GH_AMD64 = "62544b0f3759bbf1155c0ac3d75838b5fe23d66dfb75cf8368f84fff8f82b93e"
GH_ARM64 = "a77f6d709c5100cda8e9bbb8d8b7143120121233d9102ba2f2bc254134db18dc"
COSIGN_AMD64 = "caaad125acef1cb81d58dcdc454a1e429d09a750d1e9e2b3ed1aed8964454708"
COSIGN_ARM64 = "bd0f9763bca54de88699c3656ade2f39c9a1c7a2916ff35601caf23a79be0629"


def load_module():
    spec = importlib.util.spec_from_file_location("release", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_release_inputs_accepts_matching_requested_package_version_and_main_sha() -> None:
    module = load_module()

    module.validate_release_inputs(
        requested_version="0.5.0",
        package_version="0.5.0",
        pipeline_sha=SOURCE_SHA,
        main_sha=SOURCE_SHA,
    )

    with pytest.raises(module.ReleaseError):
        module.validate_release_inputs(
            requested_version="0.3.0",
            package_version="0.5.0",
            pipeline_sha=SOURCE_SHA,
            main_sha=SOURCE_SHA,
        )


@pytest.mark.parametrize(
    ("platform", "expected"),
    [
        ("linux/amd64", ("amd64", GH_AMD64, COSIGN_AMD64)),
        ("linux/arm64", ("arm64", GH_ARM64, COSIGN_ARM64)),
    ],
)
def test_release_toolchain_selects_pinned_platform_assets(
    platform: str, expected: tuple[str, str, str]
) -> None:
    module = load_module()

    assert module.release_toolchain(platform) == expected


@pytest.mark.parametrize("platform", ["", "linux/arm", "darwin/arm64", "windows/amd64"])
def test_release_toolchain_rejects_unsupported_platform(platform: str) -> None:
    module = load_module()

    with pytest.raises(module.ReleaseError, match="unsupported release platform"):
        module.release_toolchain(platform)


def test_write_checksums_is_canonical_and_asset_discovery_is_exact_for_requested_version(
    tmp_path: Path,
) -> None:
    module = load_module()
    wheel = tmp_path / "infralink-0.4.0-py3-none-any.whl"
    sdist = tmp_path / "infralink-0.4.0.tar.gz"
    wheel.write_bytes(b"wheel\n")
    sdist.write_bytes(b"sdist\n")

    checksums = module.write_checksums(tmp_path, version="0.4.0")

    assert checksums.read_text(encoding="ascii") == (
        f"{module.sha256(wheel)}  {wheel.name}\n{module.sha256(sdist)}  {sdist.name}\n"
    )
    bundle = tmp_path / "SHA256SUMS.sigstore.json"
    bundle.write_text("{}\n", encoding="ascii")
    assert [path.name for path in module.release_assets(tmp_path, version="0.4.0")] == [
        wheel.name,
        sdist.name,
        "SHA256SUMS",
        bundle.name,
    ]


@pytest.mark.parametrize("extra_name", [None, "unexpected.whl"])
def test_write_checksums_rejects_nonexact_package_set(
    tmp_path: Path, extra_name: str | None
) -> None:
    module = load_module()
    (tmp_path / "infralink-0.4.0-py3-none-any.whl").write_bytes(b"wheel")
    if extra_name is not None:
        (tmp_path / "infralink-0.4.0.tar.gz").write_bytes(b"sdist")
        (tmp_path / extra_name).write_bytes(b"extra")

    with pytest.raises(module.ReleaseError, match="package asset set"):
        module.write_checksums(tmp_path, version="0.4.0")


def test_release_assets_rejects_missing_bundle(tmp_path: Path) -> None:
    module = load_module()
    for name in (
        "infralink-0.4.0-py3-none-any.whl",
        "infralink-0.4.0.tar.gz",
        "SHA256SUMS",
    ):
        (tmp_path / name).write_bytes(b"data")

    with pytest.raises(module.ReleaseError, match="release asset set"):
        module.release_assets(tmp_path, version="0.4.0")


def test_release_contract_derives_tag_and_assets_from_version() -> None:
    module = load_module()

    assert module.release_tag("0.4.0") == "v0.4.0"
    assert module.package_assets("0.4.0") == (
        "infralink-0.4.0-py3-none-any.whl",
        "infralink-0.4.0.tar.gz",
    )
