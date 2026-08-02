import importlib.util
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "release_v0_2.py"
SOURCE_SHA = "a" * 40


def load_module():
    spec = importlib.util.spec_from_file_location("release_v0_2", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_validate_release_inputs_accepts_exact_version_and_main_sha() -> None:
    module = load_module()

    module.validate_release_inputs(
        requested_version="0.2.0",
        package_version="0.2.0",
        pipeline_sha=SOURCE_SHA,
        main_sha=SOURCE_SHA,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"requested_version": "0.2.1"},
        {"package_version": "0.2.1"},
        {"pipeline_sha": "b" * 40},
        {"pipeline_sha": "not-a-sha", "main_sha": "not-a-sha"},
        {"main_sha": "A" * 40},
    ],
)
def test_validate_release_inputs_rejects_nonexact_identity(overrides: dict[str, str]) -> None:
    module = load_module()
    inputs = {
        "requested_version": "0.2.0",
        "package_version": "0.2.0",
        "pipeline_sha": SOURCE_SHA,
        "main_sha": SOURCE_SHA,
    }
    inputs.update(overrides)

    with pytest.raises(module.ReleaseError):
        module.validate_release_inputs(**inputs)


def test_write_checksums_is_canonical_and_asset_discovery_is_exact(tmp_path: Path) -> None:
    module = load_module()
    wheel = tmp_path / "infralink-0.2.0-py3-none-any.whl"
    sdist = tmp_path / "infralink-0.2.0.tar.gz"
    wheel.write_bytes(b"wheel\n")
    sdist.write_bytes(b"sdist\n")

    checksums = module.write_checksums(tmp_path)

    assert checksums.read_text(encoding="ascii") == (
        f"{module.sha256(wheel)}  {wheel.name}\n{module.sha256(sdist)}  {sdist.name}\n"
    )
    bundle = tmp_path / "SHA256SUMS.sigstore.json"
    bundle.write_text("{}\n", encoding="ascii")
    assert [path.name for path in module.release_assets(tmp_path)] == [
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
    (tmp_path / "infralink-0.2.0-py3-none-any.whl").write_bytes(b"wheel")
    if extra_name is not None:
        (tmp_path / "infralink-0.2.0.tar.gz").write_bytes(b"sdist")
        (tmp_path / extra_name).write_bytes(b"extra")

    with pytest.raises(module.ReleaseError, match="package asset set"):
        module.write_checksums(tmp_path)


def test_release_assets_rejects_missing_bundle(tmp_path: Path) -> None:
    module = load_module()
    for name in (
        "infralink-0.2.0-py3-none-any.whl",
        "infralink-0.2.0.tar.gz",
        "SHA256SUMS",
    ):
        (tmp_path / name).write_bytes(b"data")

    with pytest.raises(module.ReleaseError, match="release asset set"):
        module.release_assets(tmp_path)
