import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "scripts" / "build_release_manifest.py"


def load_manifest_module():
    spec = importlib.util.spec_from_file_location("build_release_manifest", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_dist(dist: Path) -> tuple[Path, Path]:
    dist.mkdir()
    wheel = dist / "infralink-0.2.0-py3-none-any.whl"
    sdist = dist / "infralink-0.2.0.tar.gz"
    wheel.write_bytes(b"wheel bytes")
    sdist.write_bytes(b"sdist bytes")
    return wheel, sdist


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_build_manifest_is_pure_and_sorts_exact_release_artifacts(tmp_path: Path) -> None:
    module = load_manifest_module()
    wheel, sdist = write_dist(tmp_path / "dist")

    manifest = module.build_manifest(
        tmp_path / "dist",
        source_commit="a" * 40,
        workflow_run_id="12345",
    )

    assert manifest == {
        "version": "0.2.0",
        "source_commit": "a" * 40,
        "workflow_run_id": "12345",
        "artifacts": [
            {"name": wheel.name, "sha256": digest(wheel)},
            {"name": sdist.name, "sha256": digest(sdist)},
        ],
    }


@pytest.mark.parametrize(
    ("files", "match"),
    [
        ({"infralink-0.2.0.tar.gz": b"x"}, "exactly one wheel"),
        ({"infralink-0.2.0-py3-none-any.whl": b"x"}, "exactly one sdist"),
        (
            {
                "infralink-0.2.0-py3-none-any.whl": b"x",
                "infralink-0.2.0-1-py3-none-any.whl": b"x",
                "infralink-0.2.0.tar.gz": b"x",
            },
            "exactly one wheel",
        ),
        (
            {
                "infralink-0.2.0-py3-none-any.whl": b"x",
                "infralink-0.2.0.tar.gz": b"x",
                "other.txt": b"x",
            },
            "unexpected artifact",
        ),
        (
            {
                "infralink-0.3.0-py3-none-any.whl": b"x",
                "infralink-0.2.0.tar.gz": b"x",
            },
            "version",
        ),
    ],
)
def test_build_manifest_rejects_invalid_artifact_sets(
    tmp_path: Path, files: dict[str, bytes], match: str
) -> None:
    module = load_manifest_module()
    dist = tmp_path / "dist"
    dist.mkdir()
    for name, content in files.items():
        (dist / name).write_bytes(content)

    with pytest.raises(ValueError, match=match):
        module.build_manifest(dist, source_commit="a" * 40, workflow_run_id="local")


@pytest.mark.parametrize("source_commit", ["a" * 39, "a" * 41, "A" * 40, "g" * 40, " main"])
def test_build_manifest_rejects_noncanonical_source_commit(
    tmp_path: Path, source_commit: str
) -> None:
    module = load_manifest_module()
    write_dist(tmp_path / "dist")

    with pytest.raises(ValueError, match="40 lowercase hexadecimal"):
        module.build_manifest(
            tmp_path / "dist",
            source_commit=source_commit,
            workflow_run_id="local",
        )


def test_write_release_metadata_is_byte_deterministic_and_replaces_old_outputs(
    tmp_path: Path,
) -> None:
    module = load_manifest_module()
    dist = tmp_path / "dist"
    wheel, sdist = write_dist(dist)
    output = dist / "manifest.json"
    checksums = dist / "SHA256SUMS"
    output.write_text("stale", encoding="utf-8")
    checksums.write_text("stale", encoding="utf-8")

    module.write_release_metadata(
        dist,
        source_commit="b" * 40,
        workflow_run_id="local",
        output=output,
    )
    first_manifest = output.read_bytes()
    first_checksums = checksums.read_bytes()
    module.write_release_metadata(
        dist,
        source_commit="b" * 40,
        workflow_run_id="local",
        output=output,
    )

    expected = json.dumps(json.loads(first_manifest), sort_keys=True, indent=2) + "\n"
    assert first_manifest == expected.encode()
    assert output.read_bytes() == first_manifest
    assert checksums.read_bytes() == first_checksums
    assert first_checksums.decode() == (
        f"{digest(wheel)}  {wheel.name}\n{digest(sdist)}  {sdist.name}\n"
    )
